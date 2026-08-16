"""Persistencia de autenticación, ACL, sesiones y auditoría."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from psycopg.types.json import Jsonb

from .auth_permissions import PERMISSIONS, permission_payload
from .config import settings
from .db import get_conn


TESISTA_DENIED = {
    "cajas.records.delete",
    "subcultivos.records.delete",
    "biblioteca.documents.delete",
    "datos.database.view",
    "datos.backups.manage",
    "datos.database.restore",
    "datos.database.seed",
    "datos.database.delete_all",
    "chat.groups.manage", "chat.messages.moderate",
    "tareas.items.delete", "tareas.comments.moderate", "tareas.spaces.manage",
    "tareas.config.view", "tareas.fields.manage", "tareas.workflow.manage",
    "tareas.permissions.manage", "ia.agent.act", "ia.shell.execute",
    "ia.config.view", "ia.config.manage", "ia.connectors.manage", "ia.policy.manage",
    "ia.usage.view",
}

SUPERADMIN_ONLY = frozenset({"ia.shell.execute"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalize_email(email)))


def sync_catalog() -> None:
    """Sincroniza permisos canónicos y asignaciones iniciales de roles del sistema."""
    with get_conn() as conn, conn.cursor() as cur:
        for item in PERMISSIONS:
            payload = permission_payload(item)
            endpoints = payload.pop("endpoints")
            primary = endpoints[0] if endpoints else {}
            cur.execute(
                """
                INSERT INTO permisos_acceso
                  (clave, modulo, recurso, accion, descripcion, tipo, ruta_frontend,
                   metodo_http, patron_endpoint, metadata, nivel_riesgo, es_sistema, activo)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,TRUE)
                ON CONFLICT (clave) DO UPDATE SET
                  modulo=EXCLUDED.modulo, recurso=EXCLUDED.recurso, accion=EXCLUDED.accion,
                  descripcion=EXCLUDED.descripcion, tipo=EXCLUDED.tipo,
                  ruta_frontend=EXCLUDED.ruta_frontend, metodo_http=EXCLUDED.metodo_http,
                  patron_endpoint=EXCLUDED.patron_endpoint, metadata=EXCLUDED.metadata,
                  nivel_riesgo=EXCLUDED.nivel_riesgo, es_sistema=TRUE
                """,
                (
                    item.key,
                    item.module,
                    item.resource,
                    item.action,
                    item.description,
                    item.kind,
                    item.frontend_route,
                    primary.get("method"),
                    primary.get("pattern"),
                    Jsonb({"endpoints": endpoints}),
                    item.risk,
                ),
            )
        cur.execute(
            """
            INSERT INTO roles_acceso (clave,nombre,descripcion,es_sistema,activo)
            VALUES
              ('administrador','Administrador','Control completo de la plataforma y su seguridad.',TRUE,TRUE),
              ('tesista','Tesista','Captura y consulta del flujo experimental.',TRUE,TRUE)
            ON CONFLICT (clave) DO UPDATE SET
              nombre=EXCLUDED.nombre, descripcion=EXCLUDED.descripcion,
              es_sistema=TRUE, activo=TRUE
            """
        )
        cur.execute(
            """
            INSERT INTO roles_permisos (id_rol,id_permiso)
            SELECT r.id_rol,p.id_permiso
            FROM roles_acceso r CROSS JOIN permisos_acceso p
            WHERE r.clave='administrador' AND p.activo
              AND p.clave <> ALL(%s)
            ON CONFLICT DO NOTHING
            """,
            (list(SUPERADMIN_ONLY),),
        )
        # La concesión inicial se lleva permiso por permiso en `roles_permisos_semilla`.
        # Antes se omitía el INSERT completo cuando el rol ya tenía cualquier permiso, así
        # que ningún permiso nuevo llegaba nunca a `tesista`. Con el registro, cada clave se
        # ofrece una sola vez: si una administradora la revoca después, no vuelve a aparecer.
        cur.execute(
            """
            WITH candidatos AS (
              SELECT r.id_rol, p.id_permiso, p.clave
              FROM roles_acceso r CROSS JOIN permisos_acceso p
              WHERE r.clave='tesista'
                AND p.activo
                AND p.clave <> ALL(%s)
                AND p.clave NOT LIKE 'security.%%'
                AND NOT EXISTS (
                  SELECT 1 FROM roles_permisos_semilla s
                  WHERE s.id_rol=r.id_rol AND s.clave_permiso=p.clave
                )
            ), otorgados AS (
              INSERT INTO roles_permisos (id_rol,id_permiso)
              SELECT id_rol,id_permiso FROM candidatos
              ON CONFLICT DO NOTHING
              RETURNING id_rol
            )
            INSERT INTO roles_permisos_semilla (id_rol,clave_permiso)
            SELECT id_rol,clave FROM candidatos
            ON CONFLICT DO NOTHING
            """,
            (list(TESISTA_DENIED),),
        )
        conn.commit()


def get_user_by_email(email: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_usuario::text AS id, nombre, correo, password_hash AS "passwordHash",
                   activo, estado_cuenta AS status, es_superadmin AS "isSuperadmin",
                   debe_cambiar_password AS "mustChangePassword", cargo,
                   avatar_uri AS "avatarUri", portada_uri AS "portadaUri",
                   institucion, departamento, grado_academico AS "gradoAcademico",
                   linea_investigacion AS "lineaInvestigacion", biografia, orcid,
                   telefono, ubicacion, enlace_personal AS "enlacePersonal",
                   intentos_fallidos AS "failedAttempts",
                   bloqueado_hasta AS "lockedUntil", ultimo_login_at AS "lastLoginAt",
                   ultima_actividad_at AS "lastSeenAt", created_at AS "createdAt",
                   updated_at AS "updatedAt"
            FROM usuarios_laboratorio WHERE lower(correo)=lower(%s)
            """,
            (normalize_email(email),),
        )
        return cur.fetchone()


def get_user_by_id(user_id: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_usuario::text AS id, nombre, correo, password_hash AS "passwordHash",
                   activo, estado_cuenta AS status, es_superadmin AS "isSuperadmin",
                   debe_cambiar_password AS "mustChangePassword", cargo,
                   avatar_uri AS "avatarUri", portada_uri AS "portadaUri",
                   institucion, departamento, grado_academico AS "gradoAcademico",
                   linea_investigacion AS "lineaInvestigacion", biografia, orcid,
                   telefono, ubicacion, enlace_personal AS "enlacePersonal",
                   intentos_fallidos AS "failedAttempts",
                   bloqueado_hasta AS "lockedUntil", ultimo_login_at AS "lastLoginAt",
                   ultima_actividad_at AS "lastSeenAt", created_at AS "createdAt",
                   updated_at AS "updatedAt"
            FROM usuarios_laboratorio WHERE id_usuario=%s
            """,
            (user_id,),
        )
        return cur.fetchone()


def _keys(cur, sql: str, user_id: str) -> list[str]:
    cur.execute(sql, (user_id,))
    return [row["clave"] for row in cur.fetchall()]


def effective_permissions(user_id: str, is_superadmin: bool = False) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        if is_superadmin:
            cur.execute("SELECT clave FROM permisos_acceso WHERE activo ORDER BY clave")
            return [row["clave"] for row in cur.fetchall()]
        allowed: set[str] = set()
        denied: set[str] = set()
        allowed.update(
            _keys(
                cur,
                """
                SELECT DISTINCT p.clave FROM permisos_acceso p
                JOIN roles_permisos rp ON rp.id_permiso=p.id_permiso
                JOIN usuarios_roles ur ON ur.id_rol=rp.id_rol
                JOIN roles_acceso r ON r.id_rol=ur.id_rol
                WHERE ur.id_usuario=%s AND p.activo AND r.activo
                """,
                user_id,
            )
        )
        allowed.update(
            _keys(
                cur,
                """
                SELECT DISTINCT p.clave FROM permisos_acceso p
                JOIN roles_permisos rp ON rp.id_permiso=p.id_permiso
                JOIN grupos_roles gr ON gr.id_rol=rp.id_rol
                JOIN grupos_miembros gm ON gm.id_grupo=gr.id_grupo
                JOIN grupos_acceso g ON g.id_grupo=gm.id_grupo
                JOIN roles_acceso r ON r.id_rol=gr.id_rol
                WHERE gm.id_usuario=%s AND p.activo AND g.activo AND r.activo
                """,
                user_id,
            )
        )
        cur.execute(
            """
            SELECT p.clave, up.efecto FROM usuarios_permisos up
            JOIN permisos_acceso p ON p.id_permiso=up.id_permiso
            WHERE up.id_usuario=%s AND p.activo
            """,
            (user_id,),
        )
        for row in cur.fetchall():
            (allowed if row["efecto"] == "allow" else denied).add(row["clave"])
        cur.execute(
            """
            SELECT p.clave, gp.efecto FROM grupos_permisos gp
            JOIN permisos_acceso p ON p.id_permiso=gp.id_permiso
            JOIN grupos_miembros gm ON gm.id_grupo=gp.id_grupo
            JOIN grupos_acceso g ON g.id_grupo=gm.id_grupo
            WHERE gm.id_usuario=%s AND p.activo AND g.activo
            """,
            (user_id,),
        )
        for row in cur.fetchall():
            (allowed if row["efecto"] == "allow" else denied).add(row["clave"])
        return sorted(allowed - denied)


def user_roles(user_id: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id_rol::text AS id, r.clave, r.nombre
            FROM roles_acceso r JOIN usuarios_roles ur ON ur.id_rol=r.id_rol
            WHERE ur.id_usuario=%s AND r.activo ORDER BY r.nombre
            """,
            (user_id,),
        )
        return cur.fetchall()


def user_groups(user_id: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.id_grupo::text AS id, g.clave, g.nombre
            FROM grupos_acceso g JOIN grupos_miembros gm ON gm.id_grupo=g.id_grupo
            WHERE gm.id_usuario=%s AND g.activo ORDER BY g.nombre
            """,
            (user_id,),
        )
        return cur.fetchall()


def primary_role_name(user_id: str, is_superadmin: bool = False) -> str:
    if is_superadmin:
        return "Superadministradora"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT role_name AS nombre
            FROM (
              SELECT r.nombre AS role_name
              FROM usuarios_roles ur
              JOIN roles_acceso r ON r.id_rol=ur.id_rol
              WHERE ur.id_usuario=%s AND r.activo
              UNION
              SELECT r.nombre AS role_name
              FROM grupos_miembros gm
              JOIN grupos_acceso g ON g.id_grupo=gm.id_grupo
              JOIN grupos_roles gr ON gr.id_grupo=g.id_grupo
              JOIN roles_acceso r ON r.id_rol=gr.id_rol
              WHERE gm.id_usuario=%s AND g.activo AND r.activo
            ) effective_roles
            ORDER BY role_name
            LIMIT 1
            """,
            (user_id, user_id),
        )
        row = cur.fetchone()
        return row["nombre"] if row else "Sin rol"


def public_user(user: dict) -> dict:
    result = {key: value for key, value in user.items() if key not in {"passwordHash", "failedAttempts", "lockedUntil"}}
    result["roles"] = user_roles(user["id"])
    result["groups"] = user_groups(user["id"])
    result["permissions"] = effective_permissions(user["id"], bool(user.get("isSuperadmin")))
    return result


def create_user(
    *,
    name: str,
    email: str,
    password_hash: str,
    status: str = "pendiente",
    active: bool = True,
    cargo: str | None = None,
    is_superadmin: bool = False,
    must_change_password: bool = False,
) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO usuarios_laboratorio
              (nombre, correo, password_hash, estado_cuenta, activo, cargo,
               es_superadmin, debe_cambiar_password, aprobado_at, password_cambiado_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                    CASE WHEN %s='activa' THEN now() ELSE NULL END, now())
            RETURNING id_usuario::text AS id
            """,
            (
                name.strip(),
                normalize_email(email),
                password_hash,
                status,
                active,
                cargo,
                is_superadmin,
                must_change_password,
                status,
            ),
        )
        user_id = cur.fetchone()["id"]
        if status == "activa":
            role_key = "administrador" if is_superadmin else "tesista"
            cur.execute(
                """
                INSERT INTO usuarios_roles (id_usuario,id_rol)
                SELECT %s,id_rol FROM roles_acceso WHERE clave=%s
                ON CONFLICT DO NOTHING
                """,
                (user_id, role_key),
            )
        conn.commit()
    return public_user(get_user_by_id(user_id))


def mark_login_success(user_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE usuarios_laboratorio SET intentos_fallidos=0, bloqueado_hasta=NULL,
              ultimo_login_at=now(), ultima_actividad_at=now()
            WHERE id_usuario=%s
            """,
            (user_id,),
        )
        conn.commit()


def mark_login_failure(user_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE usuarios_laboratorio
            SET intentos_fallidos=intentos_fallidos+1,
                bloqueado_hasta=CASE
                  WHEN intentos_fallidos+1 >= %s THEN now() + (%s || ' minutes')::interval
                  ELSE bloqueado_hasta END
            WHERE id_usuario=%s
            """,
            (settings.AUTH_MAX_FAILED_ATTEMPTS, settings.AUTH_LOCK_MINUTES, user_id),
        )
        conn.commit()


def touch_user(user_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE usuarios_laboratorio SET ultima_actividad_at=now()
            WHERE id_usuario=%s
              AND (ultima_actividad_at IS NULL OR ultima_actividad_at < now() - interval '5 minutes')
            """,
            (user_id,),
        )
        conn.commit()


def set_password(user_id: str, password_hash: str, must_change: bool = False) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE usuarios_laboratorio
            SET password_hash=%s, debe_cambiar_password=%s, password_cambiado_at=now(),
                intentos_fallidos=0, bloqueado_hasta=NULL
            WHERE id_usuario=%s
            """,
            (password_hash, must_change, user_id),
        )
        conn.commit()


def update_own_profile(user_id: str, payload: dict) -> dict:
    """Actualiza únicamente información personal; nunca roles, correo o estado."""
    allowed = {
        "name": "nombre",
        "cargo": "cargo",
        "institucion": "institucion",
        "departamento": "departamento",
        "gradoAcademico": "grado_academico",
        "lineaInvestigacion": "linea_investigacion",
        "biografia": "biografia",
        "orcid": "orcid",
        "telefono": "telefono",
        "ubicacion": "ubicacion",
        "enlacePersonal": "enlace_personal",
    }
    sets: list[str] = []
    values: list[Any] = []
    for key, column in allowed.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str):
            value = value.strip() or None
        sets.append(f"{column}=%s")
        values.append(value)
    if sets:
        with get_conn() as conn, conn.cursor() as cur:
            values.append(user_id)
            cur.execute(
                f"UPDATE usuarios_laboratorio SET {', '.join(sets)} WHERE id_usuario=%s",
                values,
            )
            if cur.rowcount != 1:
                raise ValueError("Usuario no encontrado.")
            conn.commit()
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("Usuario no encontrado.")
    return public_user(user)


def set_profile_image(user_id: str, kind: str, storage_uri: str | None) -> dict:
    column = {"avatar": "avatar_uri", "portada": "portada_uri"}.get(kind)
    if not column:
        raise ValueError("Tipo de imagen de perfil no válido.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE usuarios_laboratorio SET {column}=%s WHERE id_usuario=%s",
            (storage_uri, user_id),
        )
        if cur.rowcount != 1:
            raise ValueError("Usuario no encontrado.")
        conn.commit()
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("Usuario no encontrado.")
    return public_user(user)


def create_session(
    user_id: str,
    refresh_hash: str,
    expires_at: datetime,
    ip: str | None,
    user_agent: str | None,
    family_id: str | None = None,
) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sesiones_usuario
              (id_usuario,refresh_token_hash,familia_token,expira_at,ip,user_agent)
            VALUES (%s,%s,COALESCE(%s::uuid,gen_random_uuid()),%s,%s,%s)
            RETURNING id_sesion::text AS id, familia_token::text AS "familyId"
            """,
            (user_id, refresh_hash, family_id, expires_at, ip, (user_agent or "")[:500]),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def get_session_by_hash(refresh_hash: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id_sesion::text AS id, s.id_usuario::text AS "userId",
                   s.familia_token::text AS "familyId", s.expira_at AS "expiresAt",
                   s.revocado_at AS "revokedAt", s.ip, s.user_agent AS "userAgent"
            FROM sesiones_usuario s WHERE s.refresh_token_hash=%s
            """,
            (refresh_hash,),
        )
        return cur.fetchone()


def get_active_session(session_id: str, user_id: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_sesion::text AS id,id_usuario::text AS "userId",
                   expira_at AS "expiresAt",revocado_at AS "revokedAt"
            FROM sesiones_usuario
            WHERE id_sesion=%s AND id_usuario=%s AND revocado_at IS NULL AND expira_at>now()
            """,
            (session_id, user_id),
        )
        return cur.fetchone()


def rotate_session(session_id: str, new_hash: str, expires_at: datetime, ip: str | None, user_agent: str | None) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sesiones_usuario SET refresh_token_hash=%s, expira_at=%s,
              ultima_actividad_at=now(), ip=COALESCE(%s,ip), user_agent=COALESCE(%s,user_agent)
            WHERE id_sesion=%s AND revocado_at IS NULL AND expira_at > now()
            RETURNING id_sesion::text AS id, id_usuario::text AS "userId",
                      familia_token::text AS "familyId"
            """,
            (new_hash, expires_at, ip, (user_agent or "")[:500], session_id),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            raise ValueError("La sesión ya no es válida.")
        return row


def revoke_session(session_id: str, reason: str = "manual") -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sesiones_usuario SET revocado_at=COALESCE(revocado_at,now()),
              motivo_revocacion=COALESCE(motivo_revocacion,%s)
            WHERE id_sesion=%s
            """,
            (reason, session_id),
        )
        conn.commit()


def revoke_user_sessions(user_id: str, reason: str, except_session: str | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sesiones_usuario SET revocado_at=now(), motivo_revocacion=%s
            WHERE id_usuario=%s AND revocado_at IS NULL
              AND (%s::uuid IS NULL OR id_sesion<>%s::uuid)
            """,
            (reason, user_id, except_session, except_session),
        )
        conn.commit()


def revoke_family(family_id: str, reason: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sesiones_usuario SET revocado_at=COALESCE(revocado_at,now()),
              motivo_revocacion=COALESCE(motivo_revocacion,%s)
            WHERE familia_token=%s
            """,
            (reason, family_id),
        )
        conn.commit()


def own_sessions(user_id: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_sesion::text AS id, emitido_at AS "issuedAt", expira_at AS "expiresAt",
                   ultima_actividad_at AS "lastSeenAt", revocado_at AS "revokedAt",
                   ip, user_agent AS "userAgent"
            FROM sesiones_usuario WHERE id_usuario=%s ORDER BY emitido_at DESC LIMIT 30
            """,
            (user_id,),
        )
        return cur.fetchall()


def audit(
    *,
    event_type: str,
    action: str,
    resource: str,
    actor_id: str | None = None,
    session_id: str | None = None,
    actor_name: str | None = None,
    permission: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    success: bool = True,
    ip: str | None = None,
    user_agent: str | None = None,
    correlation_id: str | None = None,
    details: dict | None = None,
    before: dict | None = None,
    after: dict | None = None,
    reversible: bool = False,
) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO eventos_auditoria
                  (tipo_evento,actor,id_actor,id_sesion,accion,recurso,permiso,
                   metodo_http,ruta,estado_http,exito,ip,user_agent,correlation_id,
                   detalles,before_data,after_data,reversible)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        COALESCE(%s::uuid,gen_random_uuid()),%s,%s,%s,%s)
                """,
                (
                    event_type,
                    actor_name,
                    actor_id,
                    session_id,
                    action,
                    resource,
                    permission,
                    method,
                    path,
                    status_code,
                    success,
                    ip,
                    (user_agent or "")[:500],
                    correlation_id,
                    Jsonb(details or {}),
                    Jsonb(before) if before is not None else None,
                    Jsonb(after) if after is not None else None,
                    reversible,
                ),
            )
            conn.commit()
    except Exception:
        # La bitácora nunca debe tumbar la operación principal.
        pass


def list_users() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_usuario::text AS id, nombre, correo, activo,
                   estado_cuenta AS status, es_superadmin AS "isSuperadmin",
                   debe_cambiar_password AS "mustChangePassword", cargo,
                   avatar_uri AS "avatarUri", ultimo_login_at AS "lastLoginAt",
                   ultima_actividad_at AS "lastSeenAt", created_at AS "createdAt"
            FROM usuarios_laboratorio ORDER BY created_at DESC
            """
        )
        users = cur.fetchall()
    for user in users:
        user["roles"] = user_roles(user["id"])
        user["groups"] = user_groups(user["id"])
        user["permissions"] = effective_permissions(user["id"], bool(user["isSuperadmin"]))
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.clave AS permission,up.efecto AS effect
                FROM usuarios_permisos up
                JOIN permisos_acceso p ON p.id_permiso=up.id_permiso
                WHERE up.id_usuario=%s ORDER BY p.clave
                """,
                (user["id"],),
            )
            user["directAccess"] = cur.fetchall()
    return users


def list_directory_users() -> list[dict]:
    """Directorio mínimo visible para cualquier persona autenticada.

    No incluye correo, actividad histórica ni información de seguridad. El estado
    en línea se completa por WebSocket y no se infiere de la última actividad.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              u.id_usuario::text AS id,
              u.nombre,
              u.cargo,
              u.avatar_uri AS "avatarUri",
              CASE
                WHEN u.es_superadmin THEN 'Superadministradora'
                ELSE coalesce(
                  (
                    SELECT role_name
                    FROM (
                      SELECT r.nombre AS role_name
                      FROM usuarios_roles ur
                      JOIN roles_acceso r ON r.id_rol=ur.id_rol
                      WHERE ur.id_usuario=u.id_usuario AND r.activo
                      UNION
                      SELECT r.nombre AS role_name
                      FROM grupos_miembros gm
                      JOIN grupos_acceso g ON g.id_grupo=gm.id_grupo
                      JOIN grupos_roles gr ON gr.id_grupo=g.id_grupo
                      JOIN roles_acceso r ON r.id_rol=gr.id_rol
                      WHERE gm.id_usuario=u.id_usuario AND g.activo AND r.activo
                    ) effective_roles
                    ORDER BY role_name
                    LIMIT 1
                  ),
                  'Sin rol'
                )
              END AS rol
            FROM usuarios_laboratorio u
            WHERE u.activo AND u.estado_cuenta='activa'
            ORDER BY lower(u.nombre), u.id_usuario
            """
        )
        return cur.fetchall()


def active_superadmin_count() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)::int AS total
            FROM usuarios_laboratorio
            WHERE es_superadmin AND activo AND estado_cuenta='activa'
            """
        )
        return int(cur.fetchone()["total"])


def set_superadmin(user_id: str, enabled: bool, actor_id: str) -> dict:
    """Cambia el nivel protegido sin permitir que desaparezca el último superadmin."""
    with get_conn() as conn, conn.cursor() as cur:
        # Serializa promociones/degradaciones concurrentes junto con el trigger de BD.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('fagolab:superadmin', 0))")
        cur.execute(
            """
            SELECT id_usuario::text AS id, es_superadmin AS "isSuperadmin",
                   activo, estado_cuenta AS status
            FROM usuarios_laboratorio
            WHERE id_usuario=%s
            FOR UPDATE
            """,
            (user_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError("Usuario no encontrado.")
        if enabled and (not current["activo"] or current["status"] != "activa"):
            raise ValueError("Activa la cuenta antes de convertirla en superadministradora.")
        if not enabled and current["isSuperadmin"]:
            cur.execute(
                """
                SELECT count(*)::int AS total
                FROM usuarios_laboratorio
                WHERE es_superadmin AND activo AND estado_cuenta='activa'
                  AND id_usuario<>%s
                """,
                (user_id,),
            )
            if int(cur.fetchone()["total"]) < 1:
                raise ValueError("Debe permanecer al menos una superadministradora activa.")
        cur.execute(
            "UPDATE usuarios_laboratorio SET es_superadmin=%s WHERE id_usuario=%s",
            (enabled, user_id),
        )
        if enabled:
            cur.execute(
                """
                INSERT INTO usuarios_roles (id_usuario,id_rol,asignado_por)
                SELECT %s,id_rol,%s FROM roles_acceso WHERE clave='administrador'
                ON CONFLICT DO NOTHING
                """,
                (user_id, actor_id),
            )
        conn.commit()
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("Usuario no encontrado.")
    return public_user(user)


def approve_user(user_id: str, role_ids: list[str], actor_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE usuarios_laboratorio SET estado_cuenta='activa', activo=TRUE,
              aprobado_at=now(), aprobado_por=%s
            WHERE id_usuario=%s
            """,
            (actor_id, user_id),
        )
        cur.execute("DELETE FROM usuarios_roles WHERE id_usuario=%s", (user_id,))
        if not role_ids:
            cur.execute("SELECT id_rol::text AS id FROM roles_acceso WHERE clave='tesista'")
            role_ids = [cur.fetchone()["id"]]
        for role_id in role_ids:
            cur.execute(
                """
                INSERT INTO usuarios_roles (id_usuario,id_rol,asignado_por)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
                """,
                (user_id, role_id, actor_id),
            )
        conn.commit()
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("Usuario no encontrado.")
    return public_user(user)


def update_user(user_id: str, payload: dict) -> dict:
    current = get_user_by_id(user_id)
    if not current:
        raise ValueError("Usuario no encontrado.")
    would_be_active = payload.get("active", current["activo"])
    would_be_status = payload.get("status", current["status"])
    if (
        current["isSuperadmin"]
        and current["activo"]
        and current["status"] == "activa"
        and (not would_be_active or would_be_status != "activa")
        and active_superadmin_count() <= 1
    ):
        raise ValueError("Debe permanecer al menos una superadministradora activa.")
    allowed = {
        "name": "nombre",
        "email": "correo",
        "active": "activo",
        "status": "estado_cuenta",
        "cargo": "cargo",
    }
    sets: list[str] = []
    values: list[Any] = []
    for key, column in allowed.items():
        if key in payload:
            value = payload[key]
            if key == "email":
                value = normalize_email(str(value))
            sets.append(f"{column}=%s")
            values.append(value)
    if sets:
        with get_conn() as conn, conn.cursor() as cur:
            values.append(user_id)
            cur.execute(f"UPDATE usuarios_laboratorio SET {', '.join(sets)} WHERE id_usuario=%s", values)
            conn.commit()
    user = get_user_by_id(user_id)
    return public_user(user)


def replace_user_access(
    user_id: str,
    *,
    role_ids: list[str],
    group_ids: list[str],
    direct_permissions: list[dict],
    actor_id: str,
) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM usuarios_roles WHERE id_usuario=%s", (user_id,))
        cur.execute("DELETE FROM grupos_miembros WHERE id_usuario=%s", (user_id,))
        cur.execute("DELETE FROM usuarios_permisos WHERE id_usuario=%s", (user_id,))
        for role_id in role_ids:
            cur.execute(
                "INSERT INTO usuarios_roles (id_usuario,id_rol,asignado_por) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (user_id, role_id, actor_id),
            )
        for group_id in group_ids:
            cur.execute(
                "INSERT INTO grupos_miembros (id_grupo,id_usuario,asignado_por) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (group_id, user_id, actor_id),
            )
        for item in direct_permissions:
            if item.get("effect") not in {"allow", "deny"}:
                continue
            cur.execute(
                """
                INSERT INTO usuarios_permisos (id_usuario,id_permiso,efecto,asignado_por)
                SELECT %s,id_permiso,%s,%s FROM permisos_acceso WHERE clave=%s
                ON CONFLICT (id_usuario,id_permiso) DO UPDATE SET efecto=EXCLUDED.efecto,
                  asignado_por=EXCLUDED.asignado_por
                """,
                (user_id, item["effect"], actor_id, item.get("permission")),
            )
        conn.commit()
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("Usuario no encontrado.")
    return public_user(user)


def list_roles() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id_rol::text AS id,r.clave,r.nombre,coalesce(r.descripcion,'') AS descripcion,
                   r.es_sistema AS "isSystem",r.activo,
                   count(DISTINCT ur.id_usuario)::int AS "userCount"
            FROM roles_acceso r LEFT JOIN usuarios_roles ur ON ur.id_rol=r.id_rol
            GROUP BY r.id_rol ORDER BY r.es_sistema DESC,r.nombre
            """
        )
        roles = cur.fetchall()
        for role in roles:
            cur.execute(
                """
                SELECT p.clave FROM permisos_acceso p
                JOIN roles_permisos rp ON rp.id_permiso=p.id_permiso
                WHERE rp.id_rol=%s ORDER BY p.clave
                """,
                (role["id"],),
            )
            role["permissions"] = [row["clave"] for row in cur.fetchall()]
        return roles


def create_role(payload: dict, actor_id: str) -> dict:
    key = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("key") or payload.get("name") or "").strip().lower()).strip("_")
    if not key:
        raise ValueError("La clave del rol es obligatoria.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO roles_acceso (clave,nombre,descripcion,es_sistema)
            VALUES (%s,%s,%s,FALSE) RETURNING id_rol::text AS id
            """,
            (key, str(payload.get("name") or key).strip(), payload.get("description")),
        )
        role_id = cur.fetchone()["id"]
        conn.commit()
    replace_role_permissions(role_id, payload.get("permissions") or [], actor_id)
    return next(role for role in list_roles() if role["id"] == role_id)


def update_role(role_id: str, payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT clave FROM roles_acceso WHERE id_rol=%s", (role_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Rol no encontrado.")
        if current["clave"] == "administrador" and payload.get("active") is False:
            raise ValueError("El rol Administrador no se puede desactivar.")
        cur.execute(
            """
            UPDATE roles_acceso SET nombre=COALESCE(%s,nombre),
              descripcion=COALESCE(%s,descripcion), activo=COALESCE(%s,activo)
            WHERE id_rol=%s
            """,
            (payload.get("name"), payload.get("description"), payload.get("active"), role_id),
        )
        conn.commit()
    return next((role for role in list_roles() if role["id"] == role_id), {})


def replace_role_permissions(role_id: str, permission_keys: list[str], actor_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT clave FROM roles_acceso WHERE id_rol=%s", (role_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Rol no encontrado.")
        if current["clave"] == "administrador":
            raise ValueError("El rol Administrador conserva acceso completo. Crea un rol personalizado para restringir permisos.")
        cur.execute("DELETE FROM roles_permisos WHERE id_rol=%s", (role_id,))
        for key in permission_keys:
            cur.execute(
                """
                INSERT INTO roles_permisos (id_rol,id_permiso,asignado_por)
                SELECT %s,id_permiso,%s FROM permisos_acceso WHERE clave=%s
                ON CONFLICT DO NOTHING
                """,
                (role_id, actor_id, key),
            )
        conn.commit()
    return next((role for role in list_roles() if role["id"] == role_id), {})


def list_permissions() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_permiso::text AS id,clave,modulo,recurso,accion,descripcion,tipo,
                   ruta_frontend AS "frontendRoute",metodo_http AS "httpMethod",
                   patron_endpoint AS "endpointPattern",metadata,nivel_riesgo AS risk,activo
            FROM permisos_acceso ORDER BY modulo,recurso,accion
            """
        )
        return cur.fetchall()


def list_groups() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.id_grupo::text AS id,g.clave,g.nombre,coalesce(g.descripcion,'') AS descripcion,
                   g.activo,count(DISTINCT gm.id_usuario)::int AS "memberCount"
            FROM grupos_acceso g LEFT JOIN grupos_miembros gm ON gm.id_grupo=g.id_grupo
            GROUP BY g.id_grupo ORDER BY g.nombre
            """
        )
        groups = cur.fetchall()
        for group in groups:
            cur.execute(
                "SELECT id_usuario::text AS id FROM grupos_miembros WHERE id_grupo=%s",
                (group["id"],),
            )
            group["members"] = [row["id"] for row in cur.fetchall()]
            cur.execute(
                "SELECT id_rol::text AS id FROM grupos_roles WHERE id_grupo=%s",
                (group["id"],),
            )
            group["roles"] = [row["id"] for row in cur.fetchall()]
            cur.execute(
                """
                SELECT p.clave AS permission,gp.efecto AS effect
                FROM grupos_permisos gp JOIN permisos_acceso p ON p.id_permiso=gp.id_permiso
                WHERE gp.id_grupo=%s ORDER BY p.clave
                """,
                (group["id"],),
            )
            group["permissions"] = cur.fetchall()
        return groups


def create_group(payload: dict, actor_id: str) -> dict:
    key = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("key") or payload.get("name") or "").strip().lower()).strip("_")
    if not key:
        raise ValueError("La clave del grupo es obligatoria.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO grupos_acceso (clave,nombre,descripcion,creado_por)
            VALUES (%s,%s,%s,%s) RETURNING id_grupo::text AS id
            """,
            (key, str(payload.get("name") or key).strip(), payload.get("description"), actor_id),
        )
        group_id = cur.fetchone()["id"]
        conn.commit()
    replace_group_access(group_id, payload, actor_id)
    return next(group for group in list_groups() if group["id"] == group_id)


def update_group(group_id: str, payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE grupos_acceso SET nombre=COALESCE(%s,nombre),
              descripcion=COALESCE(%s,descripcion),activo=COALESCE(%s,activo)
            WHERE id_grupo=%s
            """,
            (payload.get("name"), payload.get("description"), payload.get("active"), group_id),
        )
        conn.commit()
    return next((group for group in list_groups() if group["id"] == group_id), {})


def replace_group_access(group_id: str, payload: dict, actor_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM grupos_miembros WHERE id_grupo=%s", (group_id,))
        cur.execute("DELETE FROM grupos_roles WHERE id_grupo=%s", (group_id,))
        cur.execute("DELETE FROM grupos_permisos WHERE id_grupo=%s", (group_id,))
        for user_id in payload.get("members") or []:
            cur.execute(
                "INSERT INTO grupos_miembros (id_grupo,id_usuario,asignado_por) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (group_id, user_id, actor_id),
            )
        for role_id in payload.get("roles") or []:
            cur.execute(
                "INSERT INTO grupos_roles (id_grupo,id_rol,asignado_por) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (group_id, role_id, actor_id),
            )
        for item in payload.get("permissions") or []:
            if item.get("effect") not in {"allow", "deny"}:
                continue
            cur.execute(
                """
                INSERT INTO grupos_permisos (id_grupo,id_permiso,efecto,asignado_por)
                SELECT %s,id_permiso,%s,%s FROM permisos_acceso WHERE clave=%s
                ON CONFLICT (id_grupo,id_permiso) DO UPDATE SET efecto=EXCLUDED.efecto,
                  asignado_por=EXCLUDED.asignado_por
                """,
                (group_id, item["effect"], actor_id, item.get("permission")),
            )
        conn.commit()
    return next((group for group in list_groups() if group["id"] == group_id), {})


def list_all_sessions() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id_sesion::text AS id,s.id_usuario::text AS "userId",u.nombre AS "userName",
                   u.correo AS email,s.emitido_at AS "issuedAt",s.expira_at AS "expiresAt",
                   s.ultima_actividad_at AS "lastSeenAt",s.revocado_at AS "revokedAt",
                   s.motivo_revocacion AS "revocationReason",s.ip,s.user_agent AS "userAgent"
            FROM sesiones_usuario s JOIN usuarios_laboratorio u ON u.id_usuario=s.id_usuario
            ORDER BY s.emitido_at DESC LIMIT 500
            """
        )
        return cur.fetchall()


def list_audit(
    *,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    action: str | None = None,
    user_id: str | None = None,
) -> dict:
    clauses: list[str] = []
    values: list[Any] = []
    if search:
        clauses.append("(coalesce(e.ruta,'') ILIKE %s OR coalesce(e.actor,'') ILIKE %s OR coalesce(e.recurso,'') ILIKE %s)")
        term = f"%{search}%"
        values.extend([term, term, term])
    if action:
        clauses.append("e.accion=%s")
        values.append(action)
    if user_id:
        clauses.append("e.id_actor=%s")
        values.append(user_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*)::int AS total FROM eventos_auditoria e {where}", values)
        total = cur.fetchone()["total"]
        cur.execute(
            f"""
            SELECT e.id_evento::text AS id,e.tipo_evento AS type,e.accion AS action,
                   e.recurso AS resource,e.permiso AS permission,e.metodo_http AS method,
                   e.ruta AS path,e.estado_http AS "statusCode",e.exito AS success,
                   e.ip,e.user_agent AS "userAgent",e.detalles AS details,
                   e.before_data AS "beforeData",e.after_data AS "afterData",
                   e.reversible,e.created_at AS "createdAt",
                   e.id_actor::text AS "actorId",coalesce(u.nombre,e.actor,'Sistema') AS "actorName"
            FROM eventos_auditoria e
            LEFT JOIN usuarios_laboratorio u ON u.id_usuario=e.id_actor
            {where}
            ORDER BY e.created_at DESC LIMIT %s OFFSET %s
            """,
            values + [max(1, min(limit, 500)), max(0, offset)],
        )
        return {"total": total, "items": cur.fetchall()}


def overview() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              count(*)::int AS total,
              count(*) FILTER (WHERE estado_cuenta='pendiente')::int AS pending,
              count(*) FILTER (WHERE estado_cuenta='activa' AND activo)::int AS active
            FROM usuarios_laboratorio
            """
        )
        users = cur.fetchone()
        cur.execute("SELECT count(*)::int AS n FROM sesiones_usuario WHERE revocado_at IS NULL AND expira_at>now()")
        sessions = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int AS n FROM eventos_auditoria WHERE created_at>now()-interval '24 hours'")
        events = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int AS n FROM roles_acceso WHERE activo")
        roles = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int AS n FROM grupos_acceso WHERE activo")
        groups = cur.fetchone()["n"]
    return {"users": users, "activeSessions": sessions, "events24h": events, "roles": roles, "groups": groups}
