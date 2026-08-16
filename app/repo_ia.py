"""Persistencia y configuración del agente IA."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from .crypto import decrypt_secret, encrypt_secret, secret_hint
from .db import get_conn


def get_config(include_secret: bool = False) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_configuracion::text AS id,clave,proveedor,base_url AS "baseUrl",modelo,
                   api_key_cifrada AS "apiKeyCifrada",api_key_pista AS "apiKeyPista",
                   habilitado,max_iteraciones AS "maxIteraciones",temperatura,precios,
                   verificado_at AS "verificadoAt",verificado_ok AS "verificadoOk",
                   verificado_detalle AS "verificadoDetalle",updated_at AS "updatedAt"
            FROM ia_configuracion WHERE clave='global'
            """
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("No existe la configuración global del agente.")
        if include_secret:
            row["apiKey"] = decrypt_secret(row.get("apiKeyCifrada"))
        row.pop("apiKeyCifrada", None)
        row["temperatura"] = float(row["temperatura"])
        return row


def save_config(payload: dict, actor_id: str) -> dict:
    current_key: bytes | None = None
    hint: str | None = None
    if "apiKey" in payload:
        clean = str(payload.get("apiKey") or "").strip()
        current_key = encrypt_secret(clean) if clean else None
        hint = secret_hint(clean) if clean else None
    fields = {
        "proveedor": "proveedor",
        "baseUrl": "base_url",
        "modelo": "modelo",
        "habilitado": "habilitado",
        "maxIteraciones": "max_iteraciones",
        "temperatura": "temperatura",
        "precios": "precios",
    }
    assignments, values = [], []
    for public, column in fields.items():
        if public in payload:
            assignments.append(f"{column}=%s")
            value = payload[public]
            values.append(Jsonb(value) if public == "precios" else value)
    if "apiKey" in payload:
        assignments.extend(["api_key_cifrada=%s", "api_key_pista=%s", "verificado_ok=NULL", "verificado_at=NULL"])
        values.extend([current_key, hint])
    assignments.append("updated_by=%s")
    values.append(actor_id)
    if assignments:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE ia_configuracion SET {','.join(assignments)} WHERE clave='global'",
                tuple(values),
            )
            conn.commit()
    return get_config()


def mark_verification(ok: bool, detail: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ia_configuracion SET verificado_at=now(),verificado_ok=%s,
              verificado_detalle=%s WHERE clave='global'
            """,
            (ok, detail[:1000]),
        )
        conn.commit()


def active_policy() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_politica::text AS id,clave,nombre,prompt_sistema AS "promptSistema",
                   reglas,dominios_permitidos AS "dominiosPermitidos",
                   dominios_bloqueados AS "dominiosBloqueados",
                   herramientas_habilitadas AS "herramientasHabilitadas",
                   comandos_bloqueados AS "comandosBloqueados",
                   max_iteraciones AS "maxIteraciones",activa,updated_at AS "updatedAt"
            FROM ia_politicas WHERE activa ORDER BY updated_at DESC LIMIT 1
            """
        )
        return cur.fetchone() or {}


def save_policy(policy_id: str, payload: dict) -> dict:
    fields = {
        "nombre": "nombre", "promptSistema": "prompt_sistema", "reglas": "reglas",
        "dominiosPermitidos": "dominios_permitidos", "dominiosBloqueados": "dominios_bloqueados",
        "herramientasHabilitadas": "herramientas_habilitadas",
        "comandosBloqueados": "comandos_bloqueados", "maxIteraciones": "max_iteraciones",
        "activa": "activa",
    }
    assignments, values = [], []
    for public, column in fields.items():
        if public in payload:
            assignments.append(f"{column}=%s")
            values.append(Jsonb(payload[public]) if public == "reglas" else payload[public])
    if assignments:
        values.append(policy_id)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE ia_politicas SET {','.join(assignments)} WHERE id_politica=%s", tuple(values))
            if not cur.rowcount:
                raise ValueError("Política no encontrada.")
            conn.commit()
    return active_policy()


def connectors(include_secrets: bool = False) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_conector::text AS id,clave,nombre,tipo,base_url AS "baseUrl",
                   api_key_cifrada AS "apiKeyCifrada",configuracion,habilitado,
                   updated_at AS "updatedAt" FROM ia_conectores ORDER BY nombre
            """
        )
        rows = cur.fetchall()
    for row in rows:
        raw = row.get("apiKeyCifrada")
        row["apiKeyPista"] = secret_hint(decrypt_secret(raw)) if raw else ""
        if not include_secrets:
            row.pop("apiKeyCifrada", None)
    return rows


def save_connector(connector_id: str, payload: dict) -> dict:
    fields = {
        "nombre": "nombre", "tipo": "tipo", "baseUrl": "base_url",
        "configuracion": "configuracion", "habilitado": "habilitado",
    }
    assignments, values = [], []
    for public, column in fields.items():
        if public in payload:
            assignments.append(f"{column}=%s")
            values.append(Jsonb(payload[public]) if public == "configuracion" else payload[public])
    if "apiKey" in payload:
        clean = str(payload.get("apiKey") or "").strip()
        assignments.append("api_key_cifrada=%s")
        values.append(encrypt_secret(clean) if clean else None)
    values.append(connector_id)
    with get_conn() as conn, conn.cursor() as cur:
        if assignments:
            cur.execute(f"UPDATE ia_conectores SET {','.join(assignments)} WHERE id_conector=%s", tuple(values))
            if not cur.rowcount:
                raise ValueError("Conector no encontrado.")
            conn.commit()
        cur.execute(
            """
            SELECT id_conector::text AS id,clave,nombre,tipo,base_url AS "baseUrl",
                   configuracion,habilitado,updated_at AS "updatedAt"
            FROM ia_conectores WHERE id_conector=%s
            """,
            (connector_id,),
        )
        return cur.fetchone()


def create_connector(payload: dict) -> dict:
    clean = str(payload.get("apiKey") or "").strip()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ia_conectores(
              clave,nombre,tipo,base_url,api_key_cifrada,configuracion,habilitado
            ) VALUES(%s,%s,%s,%s,%s,%s,%s)
            RETURNING id_conector::text AS id,clave,nombre,tipo,base_url AS "baseUrl",
                      configuracion,habilitado,updated_at AS "updatedAt"
            """,
            (
                payload["clave"], payload["nombre"], payload["tipo"], payload["baseUrl"],
                encrypt_secret(clean) if clean else None, Jsonb(payload.get("configuracion") or {}),
                bool(payload.get("habilitado")),
            ),
        )
        row = cur.fetchone()
        row["apiKeyPista"] = secret_hint(clean)
        conn.commit()
        return row


def list_conversations(user_id: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id_conversacion::text AS id,c.titulo,c.modo,
                   c.shell_habilitado AS "shellHabilitado",
                   c.tokens_entrada AS "tokensEntrada",c.tokens_salida AS "tokensSalida",
                   c.costo_acumulado AS "costoAcumulado",c.created_at AS "createdAt",
                   c.updated_at AS "updatedAt",
                   (SELECT contenido FROM ia_mensajes m WHERE m.id_conversacion=c.id_conversacion
                    ORDER BY m.created_at DESC LIMIT 1) AS "ultimoMensaje"
            FROM ia_conversaciones c WHERE c.id_usuario=%s ORDER BY c.updated_at DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    for row in rows:
        row["costoAcumulado"] = float(row["costoAcumulado"] or 0)
    return rows


def create_conversation(user_id: str, payload: dict) -> dict:
    mode = payload.get("modo", "ask")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ia_conversaciones(id_usuario,id_politica,titulo,modo)
            VALUES(%s,(SELECT id_politica FROM ia_politicas WHERE activa ORDER BY updated_at DESC LIMIT 1),
                   %s,%s)
            RETURNING id_conversacion::text AS id,titulo,modo,shell_habilitado AS "shellHabilitado",
                      created_at AS "createdAt",updated_at AS "updatedAt"
            """,
            (user_id, payload.get("titulo") or "Nueva conversación", mode),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def _owned_conversation(cur, conversation_id: str, user_id: str) -> dict | None:
    cur.execute(
        """
        SELECT id_conversacion::text AS id,id_usuario::text AS "idUsuario",titulo,modo,
               shell_habilitado AS "shellHabilitado",tokens_entrada AS "tokensEntrada",
               tokens_salida AS "tokensSalida",costo_acumulado AS "costoAcumulado",
               created_at AS "createdAt",updated_at AS "updatedAt"
        FROM ia_conversaciones WHERE id_conversacion=%s AND id_usuario=%s
        """,
        (conversation_id, user_id),
    )
    return cur.fetchone()


def conversation_detail(conversation_id: str, user_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        row = _owned_conversation(cur, conversation_id, user_id)
        if not row:
            raise ValueError("Conversación no encontrada.")
        cur.execute(
            """
            SELECT id_mensaje::text AS id,rol,contenido,tool_calls AS "toolCalls",metadata,
                   tokens_entrada AS "tokensEntrada",tokens_salida AS "tokensSalida",
                   costo,created_at AS "createdAt" FROM ia_mensajes
            WHERE id_conversacion=%s ORDER BY created_at
            """,
            (conversation_id,),
        )
        row["mensajes"] = cur.fetchall()
        row["costoAcumulado"] = float(row["costoAcumulado"] or 0)
        return row


def delete_conversation(conversation_id: str, user_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ia_conversaciones WHERE id_conversacion=%s AND id_usuario=%s",
            (conversation_id, user_id),
        )
        if not cur.rowcount:
            raise ValueError("Conversación no encontrada.")
        conn.commit()


def set_shell(conversation_id: str, user_id: str, enabled: bool, confirmation: str) -> dict:
    if enabled and confirmation != "HABILITAR SHELL":
        raise ValueError('Escribe exactamente "HABILITAR SHELL".')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ia_conversaciones SET shell_habilitado=%s
            WHERE id_conversacion=%s AND id_usuario=%s
            RETURNING id_conversacion::text AS id,shell_habilitado AS "shellHabilitado"
            """,
            (enabled, conversation_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Conversación no encontrada.")
        conn.commit()
        return row


def add_message(conversation_id: str, role: str, content: str | None, *, tool_calls=None, metadata=None) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ia_mensajes(id_conversacion,rol,contenido,tool_calls,metadata)
            VALUES(%s,%s,%s,%s,%s)
            RETURNING id_mensaje::text AS id,rol,contenido,tool_calls AS "toolCalls",
                      metadata,created_at AS "createdAt"
            """,
            (conversation_id, role, content, Jsonb(tool_calls or []), Jsonb(metadata or {})),
        )
        row = cur.fetchone()
        cur.execute("UPDATE ia_conversaciones SET updated_at=now() WHERE id_conversacion=%s", (conversation_id,))
        conn.commit()
        return row


def model_messages(conversation_id: str, limit: int = 60) -> list[dict]:
    policy = active_policy()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT rol,contenido,tool_calls,metadata FROM (
              SELECT rol,contenido,tool_calls,metadata,created_at FROM ia_mensajes
              WHERE id_conversacion=%s ORDER BY created_at DESC LIMIT %s
            ) recent ORDER BY created_at
            """,
            (conversation_id, limit),
        )
        rows = cur.fetchall()
    result = [{"role": "system", "content": policy.get("promptSistema") or "Eres el asistente de FagoLab."}]
    for row in rows:
        message = {"role": row["rol"], "content": row["contenido"] or ""}
        if row["tool_calls"]:
            message["tool_calls"] = row["tool_calls"]
        if row["rol"] == "tool":
            message["tool_call_id"] = (row["metadata"] or {}).get("toolCallId", "")
        result.append(message)
    return result


def create_run(conversation_id: str, user_id: str, mode: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        if not _owned_conversation(cur, conversation_id, user_id):
            raise ValueError("Conversación no encontrada.")
        cur.execute(
            """
            INSERT INTO ia_ejecuciones(id_conversacion,id_usuario,estado,modo,iniciada_at)
            VALUES(%s,%s,'ejecutando',%s,now())
            RETURNING id_ejecucion::text AS id,estado,modo,created_at AS "createdAt"
            """,
            (conversation_id, user_id, mode),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def finish_run(run_id: str, state: str, error: str | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ia_ejecuciones SET estado=%s,error=%s,finalizada_at=now() WHERE id_ejecucion=%s",
            (state, error, run_id),
        )
        conn.commit()


def create_tool_call(run_id: str, name: str, arguments: dict, approval: bool) -> dict:
    state = "propuesta" if approval else "aprobada"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ia_llamadas_herramienta(
              id_ejecucion,nombre,argumentos,estado,requiere_aprobacion
            ) VALUES(%s,%s,%s,%s,%s)
            RETURNING id_llamada::text AS id,nombre,argumentos,estado,
                      requiere_aprobacion AS "requiereAprobacion",creada_at AS "createdAt"
            """,
            (run_id, name, Jsonb(arguments), state, approval),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def resolve_tool_call(call_id: str, approved: bool, actor_id: str) -> dict:
    """Aprueba o rechaza una propuesta, sólo para la persona dueña de la ejecución.

    La confirmación humana es la única barrera de las herramientas que escriben, así que
    debe estar acotada a su dueño: sin el filtro por `id_usuario`, cualquiera con
    `ia.agent.act` podía aprobar la propuesta de otra persona y la herramienta terminaba
    ejecutándose con los permisos del solicitante original.
    """
    target = "aprobada" if approved else "rechazada"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ia_llamadas_herramienta c SET estado=%s,resuelta_at=now()
            FROM ia_ejecuciones e
            WHERE c.id_ejecucion=e.id_ejecucion
              AND c.id_llamada=%s AND c.estado='propuesta' AND e.id_usuario=%s
            RETURNING c.id_llamada::text AS id,c.nombre,c.argumentos,c.estado
            """,
            (target, call_id, actor_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("La propuesta ya fue resuelta, expiró o no te pertenece.")
        conn.commit()
        return row


def complete_tool_call(call_id: str, state: str, result: Any = None, error: str | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ia_llamadas_herramienta SET estado=%s,resultado=%s,error=%s,resuelta_at=now()
            WHERE id_llamada=%s
            """,
            (state, Jsonb(result) if result is not None else None, error, call_id),
        )
        conn.commit()


def record_shell(run_id: str, call_id: str, command: str, cwd: str | None, result: dict) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ia_ejecuciones_shell(
              id_llamada,id_ejecucion,comando,argv,cwd,stdout,stderr,codigo_salida,duracion_ms,truncado
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                call_id, run_id, command, Jsonb(result.get("argv") or []), cwd,
                result.get("stdout"), result.get("stderr"), result.get("exitCode"),
                result.get("durationMs"), bool(result.get("truncado")),
            ),
        )
        conn.commit()


def add_usage(conversation_id: str, input_tokens: int, output_tokens: int, cost: float) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ia_conversaciones SET tokens_entrada=tokens_entrada+%s,
              tokens_salida=tokens_salida+%s,costo_acumulado=costo_acumulado+%s
            WHERE id_conversacion=%s
            """,
            (input_tokens, output_tokens, Decimal(str(cost)), conversation_id),
        )
        conn.commit()


def runtime_context(conversation_id: str, user: dict) -> dict:
    from . import repo_auth
    with get_conn() as conn, conn.cursor() as cur:
        conversation = _owned_conversation(cur, conversation_id, user["id"])
        if not conversation:
            raise ValueError("Conversación no encontrada.")
    config = get_config(include_secret=True)
    policy = active_policy()
    raw_connectors = connectors(include_secrets=True)
    return {
        **user,
        "roles": user.get("roles") or repo_auth.user_roles(user["id"]),
        "groups": user.get("groups") or repo_auth.user_groups(user["id"]),
        "mode": conversation["modo"],
        "shellEnabled": conversation["shellHabilitado"],
        "config": config,
        "policy": policy,
        "connectors": {item["clave"]: item for item in raw_connectors if item["habilitado"]},
    }


def usage_summary() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)::int AS conversaciones,
                   COALESCE(sum(tokens_entrada),0)::bigint AS "tokensEntrada",
                   COALESCE(sum(tokens_salida),0)::bigint AS "tokensSalida",
                   COALESCE(sum(costo_acumulado),0) AS costo
            FROM ia_conversaciones
            """
        )
        row = cur.fetchone()
        row["costo"] = float(row["costo"] or 0)
        return row
