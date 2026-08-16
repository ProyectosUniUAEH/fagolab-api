"""Token de un solo uso para crear la primera administradora.

Contrato tipo Jenkins / instalador Kaanbal:
  - Si no hay superadmin, se emite un token (env BOOTSTRAP_TOKEN o aleatorio).
  - Se imprime una sola vez en logs; en BD solo vive el hash.
  - Al crear la cuenta se borra el hash y se marca consumed_at.
  - Después de eso bootstrap y el alta pública quedan muertos.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from . import repo_auth
from .config import settings
from .db import get_conn

_LOCK_KEY = "fagolab:bootstrap"
_TOKEN_PREFIX = "fagolab-setup-v1:"


def _digest(token: str) -> str:
    return hashlib.sha256(f"{_TOKEN_PREFIX}{token.strip()}".encode("utf-8")).hexdigest()


def _row(cur) -> dict:
    cur.execute(
        """
        SELECT token_hash, created_at, consumed_at, consumed_by
        FROM sistema_bootstrap WHERE id=1 FOR UPDATE
        """
    )
    found = cur.fetchone()
    if found:
        return found
    cur.execute("INSERT INTO sistema_bootstrap (id) VALUES (1) ON CONFLICT DO NOTHING")
    cur.execute(
        """
        SELECT token_hash, created_at, consumed_at, consumed_by
        FROM sistema_bootstrap WHERE id=1 FOR UPDATE
        """
    )
    return cur.fetchone()


def is_locked() -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT consumed_at IS NOT NULL AS locked FROM sistema_bootstrap WHERE id=1"
        )
        row = cur.fetchone()
        if row and row["locked"]:
            return True
    return repo_auth.active_superadmin_count() > 0


def public_status() -> dict:
    locked = is_locked()
    needs = (not locked) and repo_auth.active_superadmin_count() == 0
    return {
        "needsBootstrap": needs,
        "signupEnabled": False,
        "bootstrapLocked": locked,
    }


def _configured_token() -> str:
    return (
        settings.BOOTSTRAP_TOKEN
        or ""
    ).strip()


def ensure_token_on_startup() -> Optional[str]:
    """Genera o reutiliza el hash. Devuelve el plaintext SOLO si acaba de nacer."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LOCK_KEY,))
        row = _row(cur)
        if repo_auth.active_superadmin_count() > 0:
            if not row["consumed_at"]:
                cur.execute(
                    """
                    UPDATE sistema_bootstrap
                    SET consumed_at=now(), token_hash=NULL
                    WHERE id=1 AND consumed_at IS NULL
                    """
                )
                conn.commit()
            return None
        if row["consumed_at"]:
            return None
        env_token = _configured_token()
        if env_token:
            digest = _digest(env_token)
            if row["token_hash"] != digest:
                cur.execute(
                    """
                    UPDATE sistema_bootstrap
                    SET token_hash=%s, created_at=COALESCE(created_at, now())
                    WHERE id=1 AND consumed_at IS NULL
                    """,
                    (digest,),
                )
                conn.commit()
            return env_token
        if row["token_hash"]:
            return None
        token = secrets.token_urlsafe(24)
        cur.execute(
            """
            UPDATE sistema_bootstrap
            SET token_hash=%s, created_at=now()
            WHERE id=1 AND consumed_at IS NULL AND token_hash IS NULL
            """,
            (_digest(token),),
        )
        conn.commit()
        return token


def consume(token: str, user_id: str) -> None:
    provided = (token or "").strip()
    if not provided:
        raise PermissionError("Falta el token de inicio.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LOCK_KEY,))
        row = _row(cur)
        if row["consumed_at"] or repo_auth.active_superadmin_count() > 0:
            raise PermissionError("El inicio de sesión de instalación ya fue usado y destruido.")
        expected = row["token_hash"] or ""
        if not expected or not hmac.compare_digest(expected, _digest(provided)):
            raise PermissionError("Token de inicio inválido.")
        cur.execute(
            """
            UPDATE sistema_bootstrap
            SET token_hash=NULL, consumed_at=now(), consumed_by=%s
            WHERE id=1 AND consumed_at IS NULL
            """,
            (user_id,),
        )
        if cur.rowcount != 1:
            raise PermissionError("El inicio de sesión de instalación ya fue usado y destruido.")
        conn.commit()


def create_first_admin(
    *,
    token: str,
    name: str,
    email: str,
    password_hash: str,
    cargo: str | None,
) -> dict:
    """Crea la única superadministradora y destruye el token en la misma transacción."""
    provided = (token or "").strip()
    if not provided:
        raise PermissionError("Falta el token de inicio.")
    if not repo_auth.valid_email(email):
        raise ValueError("Escribe un correo válido.")
    if repo_auth.get_user_by_email(email):
        raise ValueError("Ya existe una cuenta con ese correo.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_LOCK_KEY,))
        row = _row(cur)
        if row["consumed_at"]:
            raise PermissionError("El inicio de sesión de instalación ya fue usado y destruido.")
        cur.execute(
            """
            SELECT count(*)::int AS total FROM usuarios_laboratorio
            WHERE es_superadmin AND activo AND estado_cuenta='activa'
            """
        )
        if int(cur.fetchone()["total"]) > 0:
            cur.execute(
                """
                UPDATE sistema_bootstrap
                SET consumed_at=now(), token_hash=NULL
                WHERE id=1 AND consumed_at IS NULL
                """
            )
            conn.commit()
            raise PermissionError("El inicio de sesión de instalación ya fue usado y destruido.")
        expected = row["token_hash"] or ""
        if not expected or not hmac.compare_digest(expected, _digest(provided)):
            raise PermissionError("Token de inicio inválido.")
        cur.execute(
            """
            INSERT INTO usuarios_laboratorio
              (nombre, correo, password_hash, estado_cuenta, activo, cargo,
               es_superadmin, debe_cambiar_password, aprobado_at, password_cambiado_at)
            VALUES (%s,%s,%s,'activa',TRUE,%s,TRUE,FALSE,now(),now())
            RETURNING id_usuario::text AS id
            """,
            (name.strip(), repo_auth.normalize_email(email), password_hash, cargo),
        )
        user_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO usuarios_roles (id_usuario,id_rol)
            SELECT %s,id_rol FROM roles_acceso WHERE clave='administrador'
            ON CONFLICT DO NOTHING
            """,
            (user_id,),
        )
        cur.execute(
            """
            UPDATE sistema_bootstrap
            SET token_hash=NULL, consumed_at=now(), consumed_by=%s
            WHERE id=1 AND consumed_at IS NULL
            """,
            (user_id,),
        )
        conn.commit()
    user = repo_auth.get_user_by_id(user_id)
    if not user:
        raise RuntimeError("No se pudo leer la administradora recién creada.")
    return repo_auth.public_user(user)
