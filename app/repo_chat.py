"""Persistencia del chat colaborativo.

Las comprobaciones de membresía viven aquí (no en el cliente ni en el socket) para
que REST, WebSocket y futuras automatizaciones compartan la misma autoridad.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from .db import get_conn


def _uuid(value: str, label: str = "identificador") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"El {label} no es válido.") from exc


def _clean_body(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("El mensaje debe ser texto.")
    text = value.strip()
    if not text:
        raise ValueError("El mensaje no puede estar vacío.")
    if len(text) > 12000:
        raise ValueError("El mensaje no puede exceder 12,000 caracteres.")
    return text


def is_member(conversation_id: str, user_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM conversacion_miembros
               WHERE id_conversacion=%s AND id_usuario=%s AND salido_at IS NULL""",
            (_uuid(conversation_id, "conversación"), _uuid(user_id, "usuario")),
        )
        return cur.fetchone() is not None


def list_conversations(user_id: str) -> list[dict]:
    """Devuelve la bandeja del usuario, incluyendo nombre útil en conversaciones 1:1."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id_conversacion::text AS id, c.tipo, c.nombre,
                   c.ultimo_mensaje_at AS "ultimoMensajeAt", cm.fijada,
                   COALESCE((
                     SELECT COUNT(*)::int FROM mensajes_conversacion um
                     WHERE um.id_conversacion=c.id_conversacion AND um.eliminado_at IS NULL
                       AND (cm.ultimo_leido_at IS NULL OR um.created_at > cm.ultimo_leido_at)
                       AND um.id_autor IS DISTINCT FROM %s
                   ), 0) AS "noLeidos",
                   lm.id_mensaje::text AS "ultimoMensajeId", lm.cuerpo AS "ultimoMensaje",
                   lm.tipo AS "ultimoMensajeTipo", lm.created_at AS "ultimoMensajeCreadoAt",
                   lm.id_autor::text AS "ultimoMensajeAutorId", au.nombre AS "ultimoMensajeAutor",
                   CASE WHEN c.tipo='directa' THEN (
                     SELECT jsonb_build_object('id', u.id_usuario::text, 'nombre', u.nombre,
                       'cargo', u.cargo, 'avatarUri', u.avatar_uri)
                     FROM conversacion_miembros other_cm
                     JOIN usuarios_laboratorio u ON u.id_usuario=other_cm.id_usuario
                     WHERE other_cm.id_conversacion=c.id_conversacion
                       AND other_cm.id_usuario <> %s AND other_cm.salido_at IS NULL LIMIT 1
                   ) ELSE NULL END AS "interlocutor",
                   (SELECT COUNT(*)::int FROM conversacion_miembros members
                     WHERE members.id_conversacion=c.id_conversacion AND members.salido_at IS NULL) AS "miembros"
            FROM conversacion_miembros cm
            JOIN conversaciones c ON c.id_conversacion=cm.id_conversacion
            LEFT JOIN LATERAL (
              SELECT * FROM mensajes_conversacion m
              WHERE m.id_conversacion=c.id_conversacion AND m.eliminado_at IS NULL
              ORDER BY m.created_at DESC LIMIT 1
            ) lm ON TRUE
            LEFT JOIN usuarios_laboratorio au ON au.id_usuario=lm.id_autor
            WHERE cm.id_usuario=%s AND cm.salido_at IS NULL
            ORDER BY cm.fijada DESC, c.ultimo_mensaje_at DESC NULLS LAST, c.created_at DESC
            """,
            (_uuid(user_id, "usuario"), _uuid(user_id, "usuario"), _uuid(user_id, "usuario")),
        )
        return cur.fetchall()


def get_or_create_direct(user_id: str, other_user_id: str) -> tuple[dict, bool]:
    actor, other = _uuid(user_id, "usuario"), _uuid(other_user_id, "usuario")
    if actor == other:
        raise ValueError("No puedes abrir una conversación directa contigo.")
    key = ":".join(sorted((actor, other)))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_usuario FROM usuarios_laboratorio WHERE id_usuario=%s AND activo AND estado_cuenta='activa'", (other,))
        if not cur.fetchone():
            raise LookupError("La persona no está disponible para conversar.")
        cur.execute("SELECT id_conversacion::text AS id FROM conversaciones WHERE clave_directa=%s", (key,))
        row = cur.fetchone()
        created = False
        if row:
            conversation_id = row["id"]
            cur.execute("UPDATE conversacion_miembros SET salido_at=NULL WHERE id_conversacion=%s AND id_usuario IN (%s,%s)", (conversation_id, actor, other))
        else:
            cur.execute(
                """INSERT INTO conversaciones (tipo, clave_directa, creado_por)
                   VALUES ('directa',%s,%s) RETURNING id_conversacion::text AS id""",
                (key, actor),
            )
            conversation_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO conversacion_miembros (id_conversacion,id_usuario,rol)
                   VALUES (%s,%s,'miembro'),(%s,%s,'miembro')""",
                (conversation_id, actor, conversation_id, other),
            )
            created = True
        conn.commit()
    return get_conversation(conversation_id, actor), created


def create_group(user_id: str, payload: dict) -> dict:
    name = str(payload.get("nombre") or "").strip()
    if not name or len(name) > 120:
        raise ValueError("El nombre del grupo debe tener entre 1 y 120 caracteres.")
    raw_members = payload.get("miembros") or []
    if not isinstance(raw_members, list):
        raise ValueError("Los miembros deben ser una lista.")
    actor = _uuid(user_id, "usuario")
    members = {actor, *(_uuid(value, "usuario") for value in raw_members)}
    if len(members) < 2:
        raise ValueError("Un grupo requiere al menos dos personas.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_usuario::text AS id FROM usuarios_laboratorio WHERE id_usuario = ANY(%s) AND activo AND estado_cuenta='activa'", (list(members),))
        if {row["id"] for row in cur.fetchall()} != members:
            raise LookupError("Una o más personas no están disponibles.")
        cur.execute("INSERT INTO conversaciones (tipo,nombre,creado_por) VALUES ('grupo',%s,%s) RETURNING id_conversacion::text AS id", (name, actor))
        conversation_id = cur.fetchone()["id"]
        cur.executemany(
            "INSERT INTO conversacion_miembros (id_conversacion,id_usuario,rol) VALUES (%s,%s,%s)",
            [(conversation_id, member, "propietario" if member == actor else "miembro") for member in members],
        )
        conn.commit()
    return get_conversation(conversation_id, actor)


def get_conversation(conversation_id: str, user_id: str) -> dict:
    conversation_id, user_id = _uuid(conversation_id, "conversación"), _uuid(user_id, "usuario")
    if not is_member(conversation_id, user_id):
        raise PermissionError("No perteneces a esta conversación.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.id_conversacion::text AS id,c.tipo,c.nombre,c.created_at AS "createdAt",
              c.ultimo_mensaje_at AS "ultimoMensajeAt", cm.rol AS "miRol", cm.fijada,
              jsonb_agg(jsonb_build_object('id',u.id_usuario::text,'nombre',u.nombre,
                'cargo',u.cargo,'avatarUri',u.avatar_uri,'rol',members.rol) ORDER BY u.nombre) AS miembros
              FROM conversaciones c JOIN conversacion_miembros cm ON cm.id_conversacion=c.id_conversacion
              JOIN conversacion_miembros members ON members.id_conversacion=c.id_conversacion AND members.salido_at IS NULL
              JOIN usuarios_laboratorio u ON u.id_usuario=members.id_usuario
              WHERE c.id_conversacion=%s AND cm.id_usuario=%s AND cm.salido_at IS NULL
              GROUP BY c.id_conversacion,cm.rol,cm.fijada""",
            (conversation_id, user_id),
        )
        item = cur.fetchone()
        if not item:
            raise PermissionError("No perteneces a esta conversación.")
        return item


def list_messages(conversation_id: str, user_id: str, before: str | None = None, limit: int = 50) -> dict:
    conversation_id, user_id = _uuid(conversation_id, "conversación"), _uuid(user_id, "usuario")
    if not is_member(conversation_id, user_id):
        raise PermissionError("No perteneces a esta conversación.")
    limit = max(1, min(int(limit), 100))
    before_at: datetime | None = None
    if before:
        try:
            before_at = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("El cursor de mensajes no es válido.") from exc
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.id_mensaje::text AS id,m.id_conversacion::text AS "idConversacion",m.tipo,m.cuerpo,
                m.adjuntos,m.metadata,m.responde_a::text AS "respondeA",m.created_at AS "createdAt",
                m.editado_at AS "editadoAt",m.eliminado_at AS "eliminadoAt",
                a.id_usuario::text AS "autorId",a.nombre AS "autorNombre",a.avatar_uri AS "autorAvatarUri",
                COALESCE((SELECT jsonb_agg(jsonb_build_object('emoji',r.emoji,'usuarios',r.usuarios)) FROM (
                  SELECT emoji,jsonb_agg(id_usuario::text) AS usuarios FROM mensaje_reacciones
                  WHERE id_mensaje=m.id_mensaje GROUP BY emoji) r),'[]'::jsonb) AS reacciones
               FROM mensajes_conversacion m LEFT JOIN usuarios_laboratorio a ON a.id_usuario=m.id_autor
               WHERE m.id_conversacion=%s AND (%s::timestamptz IS NULL OR m.created_at < %s)
               ORDER BY m.created_at DESC LIMIT %s""",
            (conversation_id, before_at, before_at, limit + 1),
        )
        rows = cur.fetchall()
    more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return {"items": rows, "nextBefore": rows[0]["createdAt"].isoformat() if more and rows else None}


def send_message(conversation_id: str, user_id: str, payload: dict) -> dict:
    conversation_id, user_id = _uuid(conversation_id, "conversación"), _uuid(user_id, "usuario")
    if not is_member(conversation_id, user_id):
        raise PermissionError("No perteneces a esta conversación.")
    body = _clean_body(payload.get("cuerpo"))
    reply = payload.get("respondeA")
    reply = _uuid(reply, "mensaje respondido") if reply else None
    attachments = payload.get("adjuntos") or []
    if not isinstance(attachments, list) or len(attachments) > 12:
        raise ValueError("Los adjuntos no son válidos.")
    with get_conn() as conn, conn.cursor() as cur:
        if reply:
            cur.execute("SELECT 1 FROM mensajes_conversacion WHERE id_mensaje=%s AND id_conversacion=%s", (reply, conversation_id))
            if not cur.fetchone():
                raise ValueError("El mensaje respondido no pertenece a esta conversación.")
        cur.execute(
            """INSERT INTO mensajes_conversacion (id_conversacion,id_autor,tipo,cuerpo,adjuntos,responde_a)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id_mensaje::text AS id""",
            (conversation_id, user_id, "adjunto" if attachments else "texto", body, Jsonb(attachments), reply),
        )
        message_id = cur.fetchone()["id"]
        cur.execute("UPDATE conversaciones SET ultimo_mensaje_at=now() WHERE id_conversacion=%s", (conversation_id,))
        cur.execute("UPDATE conversacion_miembros SET ultimo_leido_mensaje=%s, ultimo_leido_at=now() WHERE id_conversacion=%s AND id_usuario=%s", (message_id, conversation_id, user_id))
        conn.commit()
    return get_message(message_id, user_id)


def get_message(message_id: str, user_id: str) -> dict:
    message_id, user_id = _uuid(message_id, "mensaje"), _uuid(user_id, "usuario")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_conversacion::text AS id FROM mensajes_conversacion WHERE id_mensaje=%s", (message_id,))
        row = cur.fetchone()
    if not row or not is_member(row["id"], user_id):
        raise PermissionError("No puedes consultar este mensaje.")
    # Se consulta el registro exacto para que la publicación realtime sea autocontenida.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m.id_mensaje::text AS id,m.id_conversacion::text AS "idConversacion",m.tipo,m.cuerpo,m.adjuntos,m.metadata,m.responde_a::text AS "respondeA",m.created_at AS "createdAt",m.editado_at AS "editadoAt",m.eliminado_at AS "eliminadoAt",a.id_usuario::text AS "autorId",a.nombre AS "autorNombre",a.avatar_uri AS "autorAvatarUri",COALESCE((SELECT jsonb_agg(jsonb_build_object('emoji',r.emoji,'usuarios',r.usuarios)) FROM (SELECT emoji,jsonb_agg(id_usuario::text) AS usuarios FROM mensaje_reacciones WHERE id_mensaje=m.id_mensaje GROUP BY emoji) r),'[]'::jsonb) AS reacciones FROM mensajes_conversacion m LEFT JOIN usuarios_laboratorio a ON a.id_usuario=m.id_autor WHERE m.id_mensaje=%s""", (message_id,))
        return cur.fetchone()


def edit_message(message_id: str, user_id: str, body: Any, can_moderate: bool = False) -> dict:
    message_id, user_id = _uuid(message_id, "mensaje"), _uuid(user_id, "usuario")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_conversacion::text AS conversation_id,id_autor::text AS author_id,eliminado_at FROM mensajes_conversacion WHERE id_mensaje=%s", (message_id,))
        message = cur.fetchone()
        if not message or not is_member(message["conversation_id"], user_id): raise PermissionError("No puedes editar este mensaje.")
        if message["author_id"] != user_id and not can_moderate: raise PermissionError("Solo puedes editar tus mensajes.")
        if message["eliminado_at"]: raise ValueError("No puedes editar un mensaje eliminado.")
        cur.execute("UPDATE mensajes_conversacion SET cuerpo=%s,editado_at=now() WHERE id_mensaje=%s", (_clean_body(body), message_id))
        conn.commit()
    return get_message(message_id, user_id)


def delete_message(message_id: str, user_id: str, can_moderate: bool = False) -> dict:
    message_id, user_id = _uuid(message_id, "mensaje"), _uuid(user_id, "usuario")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_conversacion::text AS conversation_id,id_autor::text AS author_id FROM mensajes_conversacion WHERE id_mensaje=%s", (message_id,))
        message = cur.fetchone()
        if not message or not is_member(message["conversation_id"], user_id): raise PermissionError("No puedes eliminar este mensaje.")
        if message["author_id"] != user_id and not can_moderate: raise PermissionError("Solo puedes eliminar tus mensajes.")
        cur.execute("UPDATE mensajes_conversacion SET eliminado_at=now(),cuerpo='Mensaje eliminado',adjuntos='[]'::jsonb WHERE id_mensaje=%s", (message_id,))
        conn.commit()
    return {"id": message_id, "idConversacion": message["conversation_id"], "eliminadoAt": datetime.now(timezone.utc).isoformat()}


def toggle_reaction(message_id: str, user_id: str, emoji: str) -> dict:
    message_id, user_id = _uuid(message_id, "mensaje"), _uuid(user_id, "usuario")
    emoji = str(emoji or "").strip()
    if not emoji or len(emoji) > 24: raise ValueError("La reacción no es válida.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_conversacion::text AS id FROM mensajes_conversacion WHERE id_mensaje=%s", (message_id,))
        row = cur.fetchone()
        if not row or not is_member(row["id"], user_id): raise PermissionError("No puedes reaccionar a este mensaje.")
        cur.execute("SELECT 1 FROM mensaje_reacciones WHERE id_mensaje=%s AND id_usuario=%s AND emoji=%s", (message_id,user_id,emoji))
        if cur.fetchone(): cur.execute("DELETE FROM mensaje_reacciones WHERE id_mensaje=%s AND id_usuario=%s AND emoji=%s", (message_id,user_id,emoji))
        else: cur.execute("INSERT INTO mensaje_reacciones (id_mensaje,id_usuario,emoji) VALUES (%s,%s,%s)", (message_id,user_id,emoji))
        conn.commit()
    return get_message(message_id, user_id)


def mark_read(conversation_id: str, user_id: str, message_id: str | None = None) -> dict:
    conversation_id, user_id = _uuid(conversation_id, "conversación"), _uuid(user_id, "usuario")
    if not is_member(conversation_id, user_id): raise PermissionError("No perteneces a esta conversación.")
    with get_conn() as conn, conn.cursor() as cur:
        if message_id:
            message_id = _uuid(message_id, "mensaje")
            cur.execute("SELECT 1 FROM mensajes_conversacion WHERE id_mensaje=%s AND id_conversacion=%s", (message_id,conversation_id))
            if not cur.fetchone(): raise ValueError("El mensaje no pertenece a esta conversación.")
        else:
            cur.execute("SELECT id_mensaje::text AS id FROM mensajes_conversacion WHERE id_conversacion=%s ORDER BY created_at DESC LIMIT 1", (conversation_id,))
            row = cur.fetchone(); message_id = row["id"] if row else None
        cur.execute("UPDATE conversacion_miembros SET ultimo_leido_mensaje=%s,ultimo_leido_at=now() WHERE id_conversacion=%s AND id_usuario=%s", (message_id,conversation_id,user_id))
        conn.commit()
    return {"idConversacion": conversation_id,"idMensaje": message_id,"usuarioId": user_id,"at": datetime.now(timezone.utc).isoformat()}
