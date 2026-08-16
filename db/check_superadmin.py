"""Diagnóstico no destructivo de la protección de superadministración."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.db import close_pool, get_conn


def main() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT clave FROM permisos_acceso
            WHERE clave IN ('security.presence.view', 'security.superadmins.manage')
            ORDER BY clave
            """
        )
        permissions = [row["clave"] for row in cur.fetchall()]
        cur.execute(
            """
            SELECT tgname, tgenabled
            FROM pg_trigger
            WHERE tgrelid='usuarios_laboratorio'::regclass
              AND tgname LIKE 'trg_proteger_superadmin_%'
            ORDER BY tgname
            """
        )
        triggers = cur.fetchall()
        cur.execute(
            """
            SELECT count(*)::int AS users,
                   count(*) FILTER (
                     WHERE es_superadmin AND activo AND estado_cuenta='activa'
                   )::int AS superadmins
            FROM usuarios_laboratorio
            """
        )
        counts = cur.fetchone()
    guard_blocks_last = False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO usuarios_laboratorio
              (nombre, correo, activo, estado_cuenta, es_superadmin)
            VALUES ('Prueba transaccional', 'guard-check@fagolab.invalid',
                    TRUE, 'activa', TRUE)
            RETURNING id_usuario
            """
        )
        test_id = cur.fetchone()["id_usuario"]
        try:
            cur.execute(
                "UPDATE usuarios_laboratorio SET es_superadmin=FALSE WHERE id_usuario=%s",
                (test_id,),
            )
        except Exception as exc:
            guard_blocks_last = getattr(exc, "sqlstate", None) == "23514"
        finally:
            conn.rollback()
    close_pool()
    print(
        f"Permisos: {len(permissions)}/2 · triggers: {len(triggers)}/3 · "
        f"usuarios: {counts['users']} · superadmins activos: {counts['superadmins']} · "
        f"guardia transaccional: {'OK' if guard_blocks_last else 'FALLO'}"
    )
    if len(permissions) != 2 or len(triggers) != 3 or not guard_blocks_last:
        raise SystemExit("La protección de superadministración está incompleta.")


if __name__ == "__main__":
    main()
