"""Persistencia y motor transaccional de tareas tipo Jira."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from psycopg.types.json import Jsonb

from .db import get_conn
from .repo import _siguiente
from .tareas_workflow import RULE_BY_TYPE, RuleContext, evaluate


TASK_SELECT = """
SELECT t.id_tarea::text AS id, t.clave, t.titulo, t.descripcion, t.tipo, t.prioridad,
       t.id_espacio::text AS "idEspacio", e.clave AS "estadoClave",
       e.id_estado::text AS "idEstado", e.nombre AS "estadoNombre",
       e.categoria AS "estadoCategoria", e.color AS "estadoColor", e.orden AS "estadoOrden",
       t.id_asignado::text AS "idAsignado", ua.nombre AS "asignadoNombre",
       t.id_reportador::text AS "idReportador", ur.nombre AS "reportadorNombre",
       t.id_padre::text AS "idPadre", t.fecha_inicio AS "fechaInicio",
       t.fecha_limite AS "fechaLimite", t.completada_at AS "completadaAt",
       t.etiquetas, t.orden_tablero AS "ordenTablero", t.id_objeto::text AS "idObjeto",
       t.codigo_objeto AS "codigoObjeto", ol.tipo_objeto AS "tipoObjeto",
       t.id_tipo::text AS "idTipo", ti.clave AS "tipoClave", ti.nombre AS "tipoNombre",
       ti.icono AS "tipoIcono", ti.color AS "tipoColor", ti.jerarquia,
       t.created_at AS "createdAt", t.updated_at AS "updatedAt"
FROM tareas t
JOIN tareas_estados e ON e.id_estado=t.id_estado
LEFT JOIN usuarios_laboratorio ua ON ua.id_usuario=t.id_asignado
LEFT JOIN usuarios_laboratorio ur ON ur.id_usuario=t.id_reportador
LEFT JOIN objetos_laboratorio ol ON ol.id_objeto=t.id_objeto
LEFT JOIN tareas_tipos ti ON ti.id_tipo=t.id_tipo
"""


# Qué puede colgar de qué: una épica agrupa tareas, una tarea agrupa subtareas.
PADRE_ESPERADO = {"epica": None, "tarea": "epica", "subtarea": "tarea"}


def _resolve_tipo(cur, clave: str) -> dict:
    cur.execute(
        """
        SELECT id_tipo::text AS id,clave,nombre,jerarquia,id_flujo::text AS "idFlujo"
        FROM tareas_tipos WHERE clave=%s AND activo
        """,
        ((clave or "tarea").strip().lower(),),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El tipo de actividad «{clave}» no existe o está desactivado.")
    return row


def _estado_inicial(cur, tipo: dict, space: dict) -> str:
    """Estado de arranque del flujo del tipo; si no tiene, el del espacio."""
    if tipo.get("idFlujo"):
        cur.execute(
            "SELECT id_estado::text AS id FROM tareas_estados WHERE id_flujo=%s AND es_inicial",
            (tipo["idFlujo"],),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
    if not space.get("id_estado"):
        raise ValueError("El flujo del tipo no declara un estado inicial.")
    return space["id_estado"]


def _validar_jerarquia(cur, tipo: dict, id_padre: str | None) -> None:
    """Impide árboles imposibles: subtarea suelta, épica dentro de algo, o nietos."""
    esperado = PADRE_ESPERADO.get(tipo["jerarquia"])
    if not id_padre:
        if tipo["jerarquia"] == "subtarea":
            raise ValueError("Una subtarea necesita una tarea principal.")
        return
    cur.execute(
        """
        SELECT t.clave, COALESCE(ti.jerarquia,'tarea') AS jerarquia
        FROM tareas t LEFT JOIN tareas_tipos ti ON ti.id_tipo=t.id_tipo
        WHERE t.id_tarea=%s
        """,
        (id_padre,),
    )
    padre = cur.fetchone()
    if not padre:
        raise ValueError("La actividad principal no existe.")
    if esperado is None:
        raise ValueError("Una épica no puede depender de otra actividad.")
    if padre["jerarquia"] != esperado:
        raise ValueError(
            f"Una {tipo['jerarquia']} sólo puede colgar de una {esperado}; "
            f"{padre['clave']} es una {padre['jerarquia']}."
        )


def _flujo_de_tarea(cur, task: dict) -> str | None:
    """Flujo que gobierna una tarea: primero el de su tipo, si no el del espacio.

    Permite que una épica y una incidencia tengan ciclos de vida distintos dentro del
    mismo espacio, que es lo que se espera de un gestor tipo Jira.
    """
    cur.execute(
        """
        SELECT COALESCE(ti.id_flujo, e.id_flujo)::text AS id
        FROM tareas t
        JOIN tareas_espacios e ON e.id_espacio=t.id_espacio
        LEFT JOIN tareas_tipos ti ON ti.id_tipo=t.id_tipo
        WHERE t.id_tarea=%s
        """,
        (task["id"],),
    )
    row = cur.fetchone()
    return row["id"] if row else None


def _task_by_key(cur, key: str) -> dict | None:
    cur.execute(f"{TASK_SELECT} WHERE t.clave=%s", (key.upper(),))
    return cur.fetchone()


def _actor(value: dict | str) -> dict:
    return value if isinstance(value, dict) else {"id": value, "permissions": [], "roles": [], "groups": []}


def _subject_matches(cur, subject_type: str, subject_id: str | None, task: dict | None, actor: dict, space_id: str) -> bool:
    user_id = actor.get("id")
    if subject_type == "todos":
        return True
    if subject_type == "usuario":
        return subject_id == user_id
    if subject_type == "rol":
        return subject_id in {item.get("id") for item in actor.get("roles", [])}
    if subject_type == "grupo":
        return subject_id in {item.get("id") for item in actor.get("groups", [])}
    if subject_type == "asignado":
        return bool(task and task.get("idAsignado") == user_id)
    if subject_type == "reportador":
        return bool(task and task.get("idReportador") == user_id)
    if subject_type == "observador" and task:
        cur.execute(
            "SELECT 1 FROM tareas_observadores WHERE id_tarea=%s AND id_usuario=%s",
            (task["id"], user_id),
        )
        return cur.fetchone() is not None
    if subject_type == "lider_espacio":
        cur.execute(
            "SELECT 1 FROM tareas_espacios WHERE id_espacio=%s AND id_lider=%s",
            (space_id, user_id),
        )
        return cur.fetchone() is not None
    return False


def _schema_allows(cur, action: str, actor: dict, space_id: str, task: dict | None = None) -> bool:
    """La capa contextual solo restringe el ACL global; jamás concede permisos."""
    if actor.get("isSuperadmin"):
        return True
    cur.execute(
        """
        SELECT r.tipo_sujeto AS "tipoSujeto",r.id_sujeto::text AS "idSujeto",r.permitir
        FROM tareas_esquema_reglas r
        JOIN tareas_esquemas_permisos e ON e.id_esquema=r.id_esquema
        WHERE e.id_espacio=%s AND e.activo AND r.accion=%s
        ORDER BY r.orden
        """,
        (space_id, action),
    )
    rules = cur.fetchall()
    if not rules:
        return True
    matched_allow = False
    has_allow = any(rule["permitir"] for rule in rules)
    for rule in rules:
        if not _subject_matches(
            cur, rule["tipoSujeto"], rule["idSujeto"], task, actor, space_id,
        ):
            continue
        if not rule["permitir"]:
            return False
        matched_allow = True
    return matched_allow if has_allow else True


def list_spaces() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_espacio::text AS id, e.clave, e.nombre, e.descripcion,
                   e.id_flujo::text AS "idFlujo", f.nombre AS "flujoNombre",
                   e.id_lider::text AS "idLider", u.nombre AS "liderNombre"
            FROM tareas_espacios e
            LEFT JOIN tareas_flujos f ON f.id_flujo=e.id_flujo
            LEFT JOIN usuarios_laboratorio u ON u.id_usuario=e.id_lider
            WHERE e.activo ORDER BY e.nombre
            """
        )
        return cur.fetchall()


def list_states(space_id: str | None = None) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id_estado::text AS id, s.clave, s.nombre, s.categoria, s.color,
                   s.orden, s.es_inicial AS "esInicial"
            FROM tareas_estados s
            JOIN tareas_espacios e ON e.id_flujo=s.id_flujo
            WHERE (%s::uuid IS NULL OR e.id_espacio=%s::uuid)
            ORDER BY s.orden,s.nombre
            """,
            (space_id, space_id),
        )
        return cur.fetchall()


def list_tasks(user: dict | str, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    clauses = ["1=1"]
    params: list[Any] = []
    if filters.get("idEspacio"):
        clauses.append("t.id_espacio=%s")
        params.append(filters["idEspacio"])
    if filters.get("estado"):
        clauses.append("e.clave=%s")
        params.append(filters["estado"])
    if filters.get("asignado"):
        clauses.append("t.id_asignado=%s")
        params.append(filters["asignado"])
    if filters.get("q"):
        clauses.append("(t.clave ILIKE %s OR t.titulo ILIKE %s OR COALESCE(t.descripcion,'') ILIKE %s)")
        term = f"%{filters['q']}%"
        params.extend([term, term, term])
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"{TASK_SELECT} WHERE {' AND '.join(clauses)} ORDER BY e.orden,t.orden_tablero,t.created_at",
            tuple(params),
        )
        actor = _actor(user)
        return [
            task for task in cur.fetchall()
            if _schema_allows(cur, "ver", actor, task["idEspacio"], task)
        ]


def board(user: dict | str, space_id: str | None = None) -> dict:
    spaces = list_spaces()
    selected = space_id or (spaces[0]["id"] if spaces else None)
    return {
        "espacios": spaces,
        "idEspacio": selected,
        "estados": list_states(selected) if selected else [],
        "tareas": list_tasks(user, {"idEspacio": selected}) if selected else [],
    }


def _task_extras(cur, task_id: str) -> dict:
    cur.execute(
        """
        SELECT c.id_comentario::text AS id,c.cuerpo,c.id_autor::text AS "idAutor",
               u.nombre AS "autorNombre",c.editado_at AS "editadoAt",c.created_at AS "createdAt"
        FROM tareas_comentarios c LEFT JOIN usuarios_laboratorio u ON u.id_usuario=c.id_autor
        WHERE c.id_tarea=%s AND c.eliminado_at IS NULL ORDER BY c.created_at
        """,
        (task_id,),
    )
    comments = cur.fetchall()
    cur.execute(
        """
        SELECT a.id_actividad::text AS id,a.tipo,a.campo,a.antes,a.despues,a.metadata,
               a.id_actor::text AS "idActor",u.nombre AS "actorNombre",a.created_at AS "createdAt"
        FROM tareas_actividad a LEFT JOIN usuarios_laboratorio u ON u.id_usuario=a.id_actor
        WHERE a.id_tarea=%s ORDER BY a.created_at DESC LIMIT 200
        """,
        (task_id,),
    )
    activity = cur.fetchall()
    # Árbol de trabajo: hijas directas y, si la hay, la actividad de la que cuelga.
    cur.execute(
        """
        SELECT t.id_tarea::text AS id,t.clave,t.titulo,t.id_asignado::text AS "idAsignado",
               u.nombre AS "asignadoNombre",s.nombre AS "estadoNombre",s.categoria AS "estadoCategoria",
               ti.nombre AS "tipoNombre",ti.icono AS "tipoIcono",ti.color AS "tipoColor"
        FROM tareas t
        JOIN tareas_estados s ON s.id_estado=t.id_estado
        LEFT JOIN usuarios_laboratorio u ON u.id_usuario=t.id_asignado
        LEFT JOIN tareas_tipos ti ON ti.id_tipo=t.id_tipo
        WHERE t.id_padre=%s ORDER BY s.orden,t.created_at
        """,
        (task_id,),
    )
    children = cur.fetchall()
    cur.execute(
        """
        SELECT p.id_tarea::text AS id,p.clave,p.titulo,ti.nombre AS "tipoNombre",
               ti.icono AS "tipoIcono",ti.color AS "tipoColor"
        FROM tareas t JOIN tareas p ON p.id_tarea=t.id_padre
        LEFT JOIN tareas_tipos ti ON ti.id_tipo=p.id_tipo
        WHERE t.id_tarea=%s
        """,
        (task_id,),
    )
    parent = cur.fetchone()
    cur.execute(
        """
        SELECT c.id_campo::text AS "idCampo",c.clave,c.nombre,c.tipo,c.requerido,v.valor
        FROM tareas_campos c LEFT JOIN tareas_valores_campo v
          ON v.id_campo=c.id_campo AND v.id_tarea=%s
        WHERE c.id_espacio=(SELECT id_espacio FROM tareas WHERE id_tarea=%s) AND c.activo
        ORDER BY c.orden,c.nombre
        """,
        (task_id, task_id),
    )
    fields = cur.fetchall()
    cur.execute(
        """
        SELECT o.id_usuario::text AS id,u.nombre FROM tareas_observadores o
        JOIN usuarios_laboratorio u ON u.id_usuario=o.id_usuario WHERE o.id_tarea=%s
        """,
        (task_id,),
    )
    observers = cur.fetchall()
    cur.execute(
        """
        SELECT id_adjunto::text AS id,id_media::text AS "idMedia",nombre,metadata,
               created_at AS "createdAt" FROM tareas_adjuntos WHERE id_tarea=%s ORDER BY created_at
        """,
        (task_id,),
    )
    attachments = cur.fetchall()
    return {
        "comentarios": comments,
        "actividad": activity,
        "campos": fields,
        "observadores": observers,
        "adjuntos": attachments,
        "subtareas": children,
        "padre": parent,
    }


def task_detail(key: str, user: dict | str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "ver", _actor(user), task["idEspacio"], task):
            raise PermissionError("El esquema de permisos no permite ver esta tarea.")
        task.update(_task_extras(cur, task["id"]))
        return task


def _activity(cur, task_id: str, actor_id: str, kind: str, field: str | None = None, before=None, after=None, metadata=None):
    cur.execute(
        """
        INSERT INTO tareas_actividad(id_tarea,id_actor,tipo,campo,antes,despues,metadata)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            task_id,
            actor_id,
            kind,
            field,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
            Jsonb(metadata or {}),
        ),
    )


def create_task(payload: dict, actor: dict) -> dict:
    title = str(payload.get("titulo") or "").strip()
    if not title:
        raise ValueError("El título es obligatorio.")
    with get_conn() as conn, conn.cursor() as cur:
        space_key = str(payload.get("espacioClave") or "TAR").upper()
        cur.execute(
            """
            SELECT e.id_espacio,e.clave,e.id_flujo,s.id_estado
            FROM tareas_espacios e JOIN tareas_estados s ON s.id_flujo=e.id_flujo AND s.es_inicial
            WHERE e.clave=%s AND e.activo
            """,
            (space_key,),
        )
        space = cur.fetchone()
        if not space:
            raise ValueError("Espacio o estado inicial no disponible.")
        if not _schema_allows(cur, "crear", actor, str(space["id_espacio"])):
            raise PermissionError("El esquema de permisos no permite crear tareas.")
        tipo = _resolve_tipo(cur, payload.get("tipoClave") or payload.get("tipo") or "tarea")
        estado_inicial = _estado_inicial(cur, tipo, space)
        _validar_jerarquia(cur, tipo, payload.get("idPadre"))
        # El vínculo se valida antes de insertar: una tarea nunca queda apuntando a un
        # código de laboratorio que no existe.
        objeto_id, objeto_codigo = _resolve_lab_object(cur, payload.get("codigoObjeto"))
        sequence = _siguiente(cur, f"tarea:{space_key}")
        key = f"{space_key}-{sequence}"
        cur.execute(
            """
            INSERT INTO tareas(
              id_espacio,clave,titulo,descripcion,tipo,id_tipo,prioridad,id_estado,id_asignado,
              id_reportador,id_padre,fecha_inicio,fecha_limite,etiquetas,orden_tablero,
              id_objeto,codigo_objeto
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              COALESCE((SELECT max(orden_tablero)+1000 FROM tareas WHERE id_espacio=%s),1000),
              %s,%s) RETURNING id_tarea::text AS id
            """,
            (
                space["id_espacio"], key, title, payload.get("descripcion"),
                tipo["clave"], tipo["id"], payload.get("prioridad", "media"),
                estado_inicial, payload.get("idAsignado"), actor["id"], payload.get("idPadre"),
                payload.get("fechaInicio"), payload.get("fechaLimite"), payload.get("etiquetas") or [],
                space["id_espacio"], objeto_id, objeto_codigo,
            ),
        )
        task_id = cur.fetchone()["id"]
        _activity(cur, task_id, actor["id"], "creada", metadata={"clave": key})
        _observar(cur, task_id, actor["id"])
        _observar(cur, task_id, payload.get("idAsignado"))
        conn.commit()
    _publish_task(space["id_espacio"], {"action": "created", "key": key})
    asignado = payload.get("idAsignado")
    if asignado and asignado != actor["id"]:
        _notificar([asignado], {
            "tareaClave": key, "titulo": title, "motivo": "asignacion",
            "texto": f"{actor.get('nombre') or 'Alguien'} te asignó {key}.",
        })
    return task_detail(key, actor)


EDITABLE = {
    "titulo": "titulo", "descripcion": "descripcion", "tipo": "tipo", "prioridad": "prioridad",
    "idAsignado": "id_asignado", "fechaInicio": "fecha_inicio", "fechaLimite": "fecha_limite",
    "etiquetas": "etiquetas", "ordenTablero": "orden_tablero",
}

# Etiquetas legibles por tipo de objeto, para que la trazabilidad se lea en el idioma
# del laboratorio y no con el nombre técnico de la tabla.
TIPO_OBJETO_ETIQUETA = {
    "recepcion_lote": "Recepción de lote", "pez": "Pez", "muestra_biologica": "Muestra",
    "caja_petri": "Caja Petri", "colonia": "Colonia", "subcultivo_petri": "Subcultivo",
    "aislamiento_bacteriano": "Aislamiento", "stock_bacteriano": "Stock bacteriano",
    "extraccion_adn": "Extracción de ADN", "vial_adn": "Vial de ADN",
    "pcr_reaccion": "Reacción PCR", "gel_electroforesis": "Gel", "secuenciacion": "Secuenciación",
    "fago": "Fago", "otro": "Objeto",
}


def _resolve_lab_object(cur, codigo: str | None) -> tuple[str | None, str | None]:
    """Traduce un código de laboratorio a (id_objeto, codigo) validando que exista.

    `codigo_objeto` era texto libre sin comprobar, así que una tarea podía apuntar a un
    código inexistente y la trazabilidad quedaba rota en silencio. Resolver contra
    `objetos_laboratorio` garantiza que el vínculo lleve siempre a una ficha real.
    """
    limpio = (codigo or "").strip().upper()
    if not limpio:
        return None, None
    cur.execute(
        "SELECT id_objeto::text AS id, codigo FROM objetos_laboratorio WHERE upper(codigo)=%s",
        (limpio,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"No existe ningún registro de laboratorio con el código {limpio}.")
    return row["id"], row["codigo"]


def buscar_objetos(termino: str, limite: int = 20) -> list[dict]:
    """Busca objetos rastreables por código para el selector de vínculo."""
    patron = f"%{(termino or '').strip()}%"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id_objeto::text AS id, o.codigo, o.tipo_objeto AS "tipoObjeto", o.nombre,
                   count(t.id_tarea)::int AS "tareasVinculadas"
            FROM objetos_laboratorio o
            LEFT JOIN tareas t ON t.id_objeto=o.id_objeto
            WHERE o.codigo ILIKE %s OR coalesce(o.nombre,'') ILIKE %s
            GROUP BY o.id_objeto, o.codigo, o.tipo_objeto, o.nombre
            ORDER BY o.codigo
            LIMIT %s
            """,
            (patron, patron, max(1, min(limite, 50))),
        )
        return [
            {**row, "tipoEtiqueta": TIPO_OBJETO_ETIQUETA.get(row["tipoObjeto"], "Objeto")}
            for row in cur.fetchall()
        ]


def tareas_de_objeto(codigo: str, actor: dict) -> dict:
    """Tareas vinculadas a un código de laboratorio: la trazabilidad en sentido inverso."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_objeto::text AS id, codigo, tipo_objeto AS "tipoObjeto", nombre
            FROM objetos_laboratorio WHERE upper(codigo)=%s
            """,
            ((codigo or "").strip().upper(),),
        )
        objeto = cur.fetchone()
        if not objeto:
            raise ValueError("No existe ese registro de laboratorio.")
        objeto["tipoEtiqueta"] = TIPO_OBJETO_ETIQUETA.get(objeto["tipoObjeto"], "Objeto")
        cur.execute(
            f"{TASK_SELECT} WHERE t.id_objeto=%s ORDER BY e.orden, t.created_at DESC",
            (objeto["id"],),
        )
        tareas = [row for row in cur.fetchall() if _schema_allows(cur, "ver", actor, row["idEspacio"], row)]
        return {"objeto": objeto, "tareas": tareas}


def update_task(key: str, payload: dict, actor: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "editar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema de permisos no permite editar esta tarea.")
        assignments, values = [], []
        if "codigoObjeto" in payload:
            # Se resuelven ambas columnas a la vez para que `id_objeto` y `codigo_objeto`
            # no puedan quedar desincronizados.
            objeto_id, objeto_codigo = _resolve_lab_object(cur, payload.get("codigoObjeto"))
            assignments += ["id_objeto=%s", "codigo_objeto=%s"]
            values += [objeto_id, objeto_codigo]
            _activity(
                cur, task["id"], actor["id"], "vinculo_laboratorio", "codigoObjeto",
                task.get("codigoObjeto"), objeto_codigo,
            )
        for public, column in EDITABLE.items():
            if public not in payload:
                continue
            assignments.append(f"{column}=%s")
            values.append(payload[public])
            _activity(cur, task["id"], actor["id"], "campo_actualizado", public, task.get(public), payload[public])
        if assignments:
            values.append(task["id"])
            cur.execute(f"UPDATE tareas SET {','.join(assignments)} WHERE id_tarea=%s", tuple(values))
        if payload.get("idAsignado"):
            _observar(cur, task["id"], payload["idAsignado"])
        for field_id, value in (payload.get("campos") or {}).items():
            cur.execute(
                """
                INSERT INTO tareas_valores_campo(id_tarea,id_campo,valor) VALUES(%s,%s,%s)
                ON CONFLICT(id_tarea,id_campo) DO UPDATE SET valor=EXCLUDED.valor,updated_at=now()
                """,
                (task["id"], field_id, Jsonb(value)),
            )
        conn.commit()
    _publish_task(task["idEspacio"], {"action": "updated", "key": key.upper()})
    nuevo = payload.get("idAsignado")
    if nuevo and nuevo != task.get("idAsignado") and nuevo != actor["id"]:
        _notificar([nuevo], {
            "tareaClave": key.upper(), "titulo": payload.get("titulo") or task["titulo"],
            "motivo": "asignacion",
            "texto": f"{actor.get('nombre') or 'Alguien'} te asignó {key.upper()}.",
        })
    return task_detail(key, actor)


def add_comment(key: str, payload: dict, actor: dict) -> dict:
    body = str(payload.get("cuerpo") or "").strip()
    if not body:
        raise ValueError("El comentario no puede estar vacío.")
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "comentar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema de permisos no permite comentar esta tarea.")
        cur.execute(
            """
            INSERT INTO tareas_comentarios(id_tarea,id_autor,cuerpo)
            VALUES(%s,%s,%s) RETURNING id_comentario::text AS id,cuerpo,created_at AS "createdAt"
            """,
            (task["id"], actor["id"], body),
        )
        comment = cur.fetchone()
        _activity(cur, task["id"], actor["id"], "comentario", metadata={"idComentario": comment["id"]})
        conn.commit()
    _publish_task(task["idEspacio"], {"action": "commented", "key": key.upper()})
    return comment


def _rule_context(cur, task: dict, actor: dict, payload: dict) -> RuleContext:
    extras = _task_extras(cur, task["id"])
    cur.execute(
        """
        SELECT count(*)::int AS total FROM tareas sub JOIN tareas_estados s ON s.id_estado=sub.id_estado
        WHERE sub.id_padre=%s AND s.categoria <> 'hecho'
        """,
        (task["id"],),
    )
    custom = {item["clave"]: item["valor"] for item in extras["campos"]}
    return RuleContext(
        task=task,
        user=actor,
        permissions=set(actor.get("permissions") or []),
        custom_fields=custom,
        comments=extras["comentarios"],
        attachments=extras["adjuntos"],
        open_subtasks=cur.fetchone()["total"],
        payload=payload,
    )


def legal_transitions(key: str, actor: dict) -> list[dict]:
    """Devuelve las transiciones del estado actual, disponibles y bloqueadas.

    Antes se descartaban en silencio las bloqueadas, así que el tablero se quedaba sin
    salidas y nadie sabía por qué. Ahora cada transición viaja con ``disponible`` y, si
    está bloqueada, con el ``motivo`` de la regla que la frena: la interfaz puede
    mostrarla desactivada y explicar qué falta para desbloquearla.
    """
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        schema_ok = _schema_allows(cur, "transicionar", actor, task["idEspacio"], task)
        cur.execute(
            """
            SELECT tr.id_transicion::text AS id,tr.clave,tr.nombre,
                   d.id_estado::text AS "idEstadoDestino",d.clave AS "estadoDestinoClave",
                   d.nombre AS "estadoDestinoNombre"
            FROM tareas_transiciones tr
            JOIN tareas_estados d ON d.id_estado=tr.id_estado_destino
            WHERE tr.id_flujo=%s AND tr.activo
              AND (tr.id_estado_origen IS NULL OR tr.id_estado_origen=%s)
            ORDER BY tr.orden,tr.nombre
            """,
            (_flujo_de_tarea(cur, task), task["idEstado"]),
        )
        transitions = cur.fetchall()
        ctx = _rule_context(cur, task, actor, {})
        result = []
        for transition in transitions:
            rules = _transition_rules(cur, transition["id"])
            transition["requiereComentario"] = any(
                rule["tipo"] == "comentario_requerido" for rule in rules
            )
            transition["disponible"] = schema_ok
            transition["motivo"] = None if schema_ok else (
                "El esquema de permisos del espacio no te permite mover esta tarea."
            )
            transition["bloqueadaPor"] = None if schema_ok else "esquema_permisos"
            if schema_ok:
                for rule in (item for item in rules if item["fase"] == "condicion"):
                    outcome = evaluate(rule, ctx)
                    if not outcome.ok:
                        transition["disponible"] = False
                        transition["motivo"] = outcome.message
                        transition["bloqueadaPor"] = rule["tipo"]
                        break
            # Los validadores no bloquean la visibilidad, pero anticiparlos evita que la
            # persona pulse y reciba un 422: la interfaz ya sabe qué le van a pedir.
            # `comentario_requerido` se omite porque su dato llega al pulsar, no antes.
            pendientes: list[str] = []
            if transition["disponible"]:
                for rule in rules:
                    if rule["fase"] != "validador" or rule["tipo"] == "comentario_requerido":
                        continue
                    outcome = evaluate(rule, ctx)
                    if not outcome.ok:
                        pendientes.append(outcome.message)
            transition["pendientes"] = pendientes
            result.append(transition)
        return result


def _transition_rules(cur, transition_id: str) -> list[dict]:
    cur.execute(
        """
        SELECT id_regla::text AS id,fase,tipo,configuracion,mensaje_error AS "mensajeError",orden
        FROM tareas_reglas_transicion WHERE id_transicion=%s AND activo ORDER BY fase,orden
        """,
        (transition_id,),
    )
    return cur.fetchall()


def transition_task(key: str, transition_id: str, payload: dict, actor: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "transicionar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema de permisos del espacio no te permite mover esta tarea.")
        cur.execute(
            """
            SELECT tr.id_transicion::text AS id,tr.clave,tr.nombre,tr.id_estado_destino::text AS destino,
                   d.clave AS "destinoClave",d.categoria AS "destinoCategoria"
            FROM tareas_transiciones tr JOIN tareas_estados d ON d.id_estado=tr.id_estado_destino
            WHERE tr.id_transicion=%s AND tr.id_flujo=%s AND tr.activo
              AND (tr.id_estado_origen IS NULL OR tr.id_estado_origen=%s)
            """,
            (transition_id, _flujo_de_tarea(cur, task), task["idEstado"]),
        )
        transition = cur.fetchone()
        if not transition:
            raise PermissionError("Esa transición no existe o no sale del estado actual.")
        rules = _transition_rules(cur, transition_id)
        ctx = _rule_context(cur, task, actor, payload)
        for rule in (item for item in rules if item["fase"] == "condicion"):
            # Se devuelve el mensaje configurado en la regla, no un código opaco: quien
            # pulsa necesita saber qué condición le falta para poder resolverla.
            outcome = evaluate(rule, ctx)
            if not outcome.ok:
                raise PermissionError(outcome.message)
        for rule in (item for item in rules if item["fase"] == "validador"):
            result = evaluate(rule, ctx)
            if not result.ok:
                raise ValueError(result.message)
        completed = datetime.now(timezone.utc) if transition["destinoCategoria"] == "hecho" else None
        cur.execute(
            "UPDATE tareas SET id_estado=%s,completada_at=%s WHERE id_tarea=%s",
            (transition["destino"], completed, task["id"]),
        )
        _activity(
            cur, task["id"], actor["id"], "transicion", "estado",
            task["estadoClave"], transition["destinoClave"],
            {"idTransicion": transition["id"], "nombre": transition["nombre"]},
        )
        if payload.get("comentario"):
            cur.execute(
                "INSERT INTO tareas_comentarios(id_tarea,id_autor,cuerpo) VALUES(%s,%s,%s)",
                (task["id"], actor["id"], payload["comentario"]),
            )
        avisar = False
        for rule in (item for item in rules if item["fase"] == "post_funcion"):
            result = evaluate(rule, ctx)
            for field, value in result.changes.items():
                if field == "idAsignado" and value:
                    cur.execute("UPDATE tareas SET id_asignado=%s WHERE id_tarea=%s", (value, task["id"]))
                    _observar(cur, task["id"], value)
                elif field == "comment" and value:
                    cur.execute(
                        "INSERT INTO tareas_comentarios(id_tarea,cuerpo) VALUES(%s,%s)",
                        (task["id"], value),
                    )
                elif field == "observer" and value:
                    _observar(cur, task["id"], value)
                elif field == "notify" and value:
                    # `notificar_observadores` emitía esta señal y nadie la escuchaba,
                    # así que la post-función quedaba decorativa.
                    avisar = True
        observadores: list[str] = []
        if avisar:
            cur.execute(
                "SELECT id_usuario::text AS id FROM tareas_observadores WHERE id_tarea=%s",
                (task["id"],),
            )
            observadores = [row["id"] for row in cur.fetchall() if row["id"] != actor["id"]]
        conn.commit()
    _publish_task(task["idEspacio"], {"action": "transitioned", "key": key.upper(), "transition": transition["clave"]})
    if observadores:
        _notificar(observadores, {
            "tareaClave": key.upper(), "titulo": task["titulo"], "motivo": "transicion",
            "texto": f"{key.upper()} pasó a {transition['destinoClave'].replace('_', ' ')}.",
        })
    return task_detail(key, actor)


def activity(space_id: str | None = None, limit: int = 100) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id_actividad::text AS id,t.clave,t.titulo,a.tipo,a.campo,a.antes,a.despues,
                   a.metadata,u.nombre AS "actorNombre",a.created_at AS "createdAt"
            FROM tareas_actividad a JOIN tareas t ON t.id_tarea=a.id_tarea
            LEFT JOIN usuarios_laboratorio u ON u.id_usuario=a.id_actor
            WHERE (%s::uuid IS NULL OR t.id_espacio=%s::uuid)
            ORDER BY a.created_at DESC LIMIT %s
            """,
            (space_id, space_id, min(max(limit, 1), 500)),
        )
        return cur.fetchall()


def config_snapshot() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_flujo::text AS id,clave,nombre,descripcion,activo FROM tareas_flujos ORDER BY nombre")
        flows = cur.fetchall()
        cur.execute(
            """
            SELECT id_estado::text AS id,id_flujo::text AS "idFlujo",clave,nombre,categoria,color,orden,
                   es_inicial AS "esInicial",pos_x AS "posX",pos_y AS "posY"
            FROM tareas_estados ORDER BY id_flujo,orden
            """
        )
        states = cur.fetchall()
        cur.execute(
            """
            SELECT id_transicion::text AS id,id_flujo::text AS "idFlujo",
                   id_estado_origen::text AS "idEstadoOrigen",id_estado_destino::text AS "idEstadoDestino",
                   clave,nombre,orden,activo FROM tareas_transiciones ORDER BY id_flujo,orden,nombre
            """
        )
        transitions = cur.fetchall()
        cur.execute(
            """
            SELECT id_regla::text AS id,id_transicion::text AS "idTransicion",fase,tipo,configuracion,
                   mensaje_error AS "mensajeError",orden,activo FROM tareas_reglas_transicion
                   ORDER BY id_transicion,fase,orden
            """
        )
        rules = cur.fetchall()
        cur.execute(
            """
            SELECT id_campo::text AS id,id_espacio::text AS "idEspacio",clave,nombre,tipo,configuracion,
                   requerido,orden,activo FROM tareas_campos ORDER BY id_espacio,orden
            """
        )
        fields = cur.fetchall()
        cur.execute(
            """
            SELECT r.id_regla::text AS id,r.id_esquema::text AS "idEsquema",r.accion,
                   r.tipo_sujeto AS "tipoSujeto",r.id_sujeto::text AS "idSujeto",
                   r.permitir,r.orden FROM tareas_esquema_reglas r ORDER BY r.id_esquema,r.orden
            """
        )
        permission_rules = cur.fetchall()
        return {
            "flujos": flows,
            "estados": states,
            "transiciones": transitions,
            "reglas": rules,
            "campos": fields,
            "reglasPermisos": permission_rules,
            "tipos": list_types(),
        }


def create_field(payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tareas_campos(id_espacio,clave,nombre,tipo,configuracion,requerido,orden)
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            RETURNING id_campo::text AS id,id_espacio::text AS "idEspacio",clave,nombre,tipo,
                      configuracion,requerido,orden,activo
            """,
            (
                payload["idEspacio"], payload["clave"], payload["nombre"], payload["tipo"],
                Jsonb(payload.get("configuracion") or {}), bool(payload.get("requerido")),
                int(payload.get("orden", 0)),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def _validar_regla(tipo: str, fase: str, configuracion: dict) -> None:
    """Comprueba la regla contra el registro del motor antes de guardarla.

    Una regla con un tipo inexistente evalúa a `False` para siempre, así que dejaría la
    transición bloqueada sin manera de arreglarlo desde la interfaz. Y un
    `campo_requerido` sin decir qué campo no exige nada: por eso se validan también las
    claves obligatorias del esquema.
    """
    definicion = RULE_BY_TYPE.get(tipo)
    if not definicion:
        raise ValueError(f"El tipo de regla «{tipo}» no existe en el motor.")
    if definicion.fase != fase:
        raise ValueError(f"«{definicion.nombre}» es una regla de {definicion.fase}, no de {fase}.")
    faltantes = [
        clave for clave in (definicion.esquema.get("required") or [])
        if configuracion.get(clave) in (None, "")
    ]
    if faltantes:
        raise ValueError(
            f"«{definicion.nombre}» necesita configurar: {', '.join(faltantes)}."
        )


def create_transition_rule(payload: dict) -> dict:
    configuracion = payload.get("configuracion") or {}
    _validar_regla(payload["tipo"], payload["fase"], configuracion)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tareas_reglas_transicion(id_transicion,fase,tipo,configuracion,mensaje_error,orden)
            VALUES(%s,%s,%s,%s,%s,%s)
            RETURNING id_regla::text AS id,id_transicion::text AS "idTransicion",
                      fase,tipo,configuracion,mensaje_error AS "mensajeError",orden,activo
            """,
            (
                payload["idTransicion"], payload["fase"], payload["tipo"],
                Jsonb(configuracion), payload.get("mensajeError"),
                int(payload.get("orden", 0)),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def update_transition_rule(rule_id: str, payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT fase,tipo,configuracion FROM tareas_reglas_transicion WHERE id_regla=%s
            """,
            (rule_id,),
        )
        actual = cur.fetchone()
        if not actual:
            raise ValueError("Regla no encontrada.")
        configuracion = payload.get("configuracion", actual["configuracion"]) or {}
        _validar_regla(payload.get("tipo", actual["tipo"]), payload.get("fase", actual["fase"]), configuracion)
        cur.execute(
            """
            UPDATE tareas_reglas_transicion
            SET tipo=COALESCE(%s,tipo),fase=COALESCE(%s,fase),configuracion=%s,
                mensaje_error=COALESCE(%s,mensaje_error),orden=COALESCE(%s,orden),
                activo=COALESCE(%s,activo)
            WHERE id_regla=%s
            RETURNING id_regla::text AS id,id_transicion::text AS "idTransicion",
                      fase,tipo,configuracion,mensaje_error AS "mensajeError",orden,activo
            """,
            (
                payload.get("tipo"), payload.get("fase"), Jsonb(configuracion),
                payload.get("mensajeError"), payload.get("orden"), payload.get("activo"), rule_id,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def create_space(payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tareas_espacios(clave,nombre,descripcion,id_flujo,id_lider)
            VALUES(upper(%s),%s,%s,%s,%s)
            RETURNING id_espacio::text AS id,clave,nombre,descripcion,
                      id_flujo::text AS "idFlujo",id_lider::text AS "idLider",activo
            """,
            (
                payload["clave"], payload["nombre"], payload.get("descripcion"),
                payload.get("idFlujo"), payload.get("idLider"),
            ),
        )
        row = cur.fetchone()
        cur.execute(
            "INSERT INTO contadores(entidad,valor) VALUES(%s,0) ON CONFLICT DO NOTHING",
            (f"tarea:{row['clave']}",),
        )
        conn.commit()
        return row


def update_space(space_id: str, payload: dict) -> dict:
    allowed = {"nombre": "nombre", "descripcion": "descripcion", "idFlujo": "id_flujo", "idLider": "id_lider", "activo": "activo"}
    assignments, values = [], []
    for public, column in allowed.items():
        if public in payload:
            assignments.append(f"{column}=%s")
            values.append(payload[public])
    if not assignments:
        raise ValueError("No hay cambios.")
    values.append(space_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE tareas_espacios SET {','.join(assignments)} WHERE id_espacio=%s
            RETURNING id_espacio::text AS id,clave,nombre,descripcion,
                      id_flujo::text AS "idFlujo",id_lider::text AS "idLider",activo
            """,
            tuple(values),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Espacio no encontrado.")
        conn.commit()
        return row


def list_types() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ti.id_tipo::text AS id,ti.clave,ti.nombre,ti.descripcion,ti.icono,ti.color,
                   ti.jerarquia,ti.id_flujo::text AS "idFlujo",f.nombre AS "flujoNombre",
                   ti.orden,ti.activo,
                   (SELECT count(*) FROM tareas t WHERE t.id_tipo=ti.id_tipo)::int AS "enUso"
            FROM tareas_tipos ti LEFT JOIN tareas_flujos f ON f.id_flujo=ti.id_flujo
            ORDER BY ti.orden,ti.nombre
            """
        )
        return cur.fetchall()


TIPO_EDITABLE = {
    "nombre": "nombre", "descripcion": "descripcion", "icono": "icono", "color": "color",
    "jerarquia": "jerarquia", "idFlujo": "id_flujo", "orden": "orden", "activo": "activo",
}


def create_type(payload: dict) -> dict:
    clave = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("clave") or "").strip().lower()).strip("_")
    if not clave:
        raise ValueError("La clave del tipo es obligatoria.")
    if str(payload.get("jerarquia", "tarea")) not in PADRE_ESPERADO:
        raise ValueError("La jerarquía debe ser epica, tarea o subtarea.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tareas_tipos(clave,nombre,descripcion,icono,color,jerarquia,id_flujo,orden)
            VALUES(%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,(SELECT COALESCE(max(orden),0)+1 FROM tareas_tipos)))
            ON CONFLICT(clave) DO NOTHING
            RETURNING id_tipo::text AS id
            """,
            (
                clave, payload.get("nombre") or clave.title(), payload.get("descripcion"),
                payload.get("icono") or "clipboard", payload.get("color") or "#1f8f7a",
                payload.get("jerarquia", "tarea"), payload.get("idFlujo"), payload.get("orden"),
            ),
        )
        if not cur.fetchone():
            raise ValueError(f"Ya existe un tipo con la clave «{clave}».")
        conn.commit()
    return next(item for item in list_types() if item["clave"] == clave)


def update_type(type_id: str, payload: dict) -> dict:
    if "jerarquia" in payload and payload["jerarquia"] not in PADRE_ESPERADO:
        raise ValueError("La jerarquía debe ser epica, tarea o subtarea.")
    assignments, values = [], []
    for public, column in TIPO_EDITABLE.items():
        if public in payload:
            assignments.append(f"{column}=%s")
            values.append(payload[public])
    if not assignments:
        raise ValueError("No hay cambios.")
    values.append(type_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE tareas_tipos SET {','.join(assignments)} WHERE id_tipo=%s", tuple(values))
        if not cur.rowcount:
            raise ValueError("Tipo no encontrado.")
        conn.commit()
    return next(item for item in list_types() if item["id"] == type_id)


def delete_type(type_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM tareas WHERE id_tipo=%s", (type_id,))
        if cur.fetchone()["n"]:
            raise ValueError("No se puede eliminar un tipo que ya tiene actividades; desactívalo.")
        cur.execute("DELETE FROM tareas_tipos WHERE id_tipo=%s", (type_id,))
        if not cur.rowcount:
            raise ValueError("Tipo no encontrado.")
        conn.commit()


def progress_by_person(space_id: str | None = None) -> dict:
    """Carga y avance por persona: qué tiene cada quien y cuánto ha cerrado."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id_usuario::text AS id,u.nombre,u.cargo,u.avatar_uri AS "avatarUri",
                   count(t.id_tarea)::int AS total,
                   count(*) FILTER (WHERE s.categoria='por_hacer')::int AS "porHacer",
                   count(*) FILTER (WHERE s.categoria='en_progreso')::int AS "enProgreso",
                   count(*) FILTER (WHERE s.categoria='hecho')::int AS hecho,
                   count(*) FILTER (WHERE s.categoria<>'hecho' AND t.fecha_limite < current_date)::int AS vencidas,
                   count(*) FILTER (WHERE t.completada_at >= now() - interval '7 days')::int AS "cerradas7d"
            FROM usuarios_laboratorio u
            JOIN tareas t ON t.id_asignado=u.id_usuario
            JOIN tareas_estados s ON s.id_estado=t.id_estado
            WHERE (%s::uuid IS NULL OR t.id_espacio=%s::uuid)
            GROUP BY u.id_usuario,u.nombre,u.cargo,u.avatar_uri
            ORDER BY count(*) FILTER (WHERE s.categoria<>'hecho') DESC, u.nombre
            """,
            (space_id, space_id),
        )
        personas = cur.fetchall()
        cur.execute(
            """
            SELECT count(*)::int AS "sinAsignar" FROM tareas t
            JOIN tareas_estados s ON s.id_estado=t.id_estado
            WHERE t.id_asignado IS NULL AND s.categoria<>'hecho'
              AND (%s::uuid IS NULL OR t.id_espacio=%s::uuid)
            """,
            (space_id, space_id),
        )
        return {"personas": personas, **cur.fetchone()}


def create_flow(payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tareas_flujos(clave,nombre,descripcion)
            VALUES(%s,%s,%s)
            RETURNING id_flujo::text AS id,clave,nombre,descripcion,activo
            """,
            (payload["clave"], payload["nombre"], payload.get("descripcion")),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def update_flow(flow_id: str, payload: dict) -> dict:
    allowed = {"nombre": "nombre", "descripcion": "descripcion", "activo": "activo"}
    assignments, values = [], []
    for public, column in allowed.items():
        if public in payload:
            assignments.append(f"{column}=%s")
            values.append(payload[public])
    if not assignments:
        raise ValueError("No hay cambios.")
    values.append(flow_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE tareas_flujos SET {','.join(assignments)} WHERE id_flujo=%s "
            "RETURNING id_flujo::text AS id,clave,nombre,descripcion,activo",
            tuple(values),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Flujo no encontrado.")
        conn.commit()
        return row


def replace_flow_states(flow_id: str, items: list[dict]) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        keep: list[str] = []
        for item in items:
            cur.execute(
                """
                INSERT INTO tareas_estados(id_estado,id_flujo,clave,nombre,categoria,color,orden,es_inicial,pos_x,pos_y)
                VALUES(COALESCE(%s::uuid,gen_random_uuid()),%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id_flujo,clave) DO UPDATE SET nombre=EXCLUDED.nombre,
                  categoria=EXCLUDED.categoria,color=EXCLUDED.color,orden=EXCLUDED.orden,
                  es_inicial=EXCLUDED.es_inicial,pos_x=EXCLUDED.pos_x,pos_y=EXCLUDED.pos_y
                RETURNING id_estado::text AS id
                """,
                (
                    item.get("id"), flow_id, item["clave"], item["nombre"], item["categoria"],
                    item.get("color"), int(item.get("orden", 0)), bool(item.get("esInicial")),
                    item.get("posX"), item.get("posY"),
                ),
            )
            keep.append(cur.fetchone()["id"])
        if keep:
            cur.execute(
                "DELETE FROM tareas_estados WHERE id_flujo=%s AND id_estado <> ALL(%s::uuid[]) "
                "AND NOT EXISTS(SELECT 1 FROM tareas WHERE tareas.id_estado=tareas_estados.id_estado)",
                (flow_id, keep),
            )
        conn.commit()
    return [item for item in config_snapshot()["estados"] if item["idFlujo"] == flow_id]


def replace_flow_transitions(flow_id: str, items: list[dict]) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        keep: list[str] = []
        for item in items:
            cur.execute(
                """
                INSERT INTO tareas_transiciones(
                  id_transicion,id_flujo,id_estado_origen,id_estado_destino,clave,nombre,orden,activo
                ) VALUES(COALESCE(%s::uuid,gen_random_uuid()),%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id_flujo,clave) DO UPDATE SET
                  id_estado_origen=EXCLUDED.id_estado_origen,id_estado_destino=EXCLUDED.id_estado_destino,
                  nombre=EXCLUDED.nombre,orden=EXCLUDED.orden,activo=EXCLUDED.activo
                RETURNING id_transicion::text AS id
                """,
                (
                    item.get("id"), flow_id, item.get("idEstadoOrigen"), item["idEstadoDestino"],
                    item["clave"], item["nombre"], int(item.get("orden", 0)), item.get("activo", True),
                ),
            )
            keep.append(cur.fetchone()["id"])
        if keep:
            cur.execute(
                "DELETE FROM tareas_transiciones WHERE id_flujo=%s AND id_transicion <> ALL(%s::uuid[])",
                (flow_id, keep),
            )
        conn.commit()
    return [item for item in config_snapshot()["transiciones"] if item["idFlujo"] == flow_id]


def list_permission_schemes() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_esquema::text AS id,e.id_espacio::text AS "idEspacio",e.nombre,e.activo,
                   COALESCE(jsonb_agg(jsonb_build_object(
                     'id',r.id_regla::text,'accion',r.accion,'tipoSujeto',r.tipo_sujeto,
                     'idSujeto',r.id_sujeto::text,'permitir',r.permitir,'orden',r.orden
                   ) ORDER BY r.orden) FILTER(WHERE r.id_regla IS NOT NULL),'[]') AS reglas
            FROM tareas_esquemas_permisos e
            LEFT JOIN tareas_esquema_reglas r ON r.id_esquema=e.id_esquema
            GROUP BY e.id_esquema ORDER BY e.nombre
            """
        )
        return cur.fetchall()


def create_permission_scheme(payload: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tareas_esquemas_permisos(id_espacio,nombre)
            VALUES(%s,%s)
            ON CONFLICT(id_espacio) DO UPDATE SET nombre=EXCLUDED.nombre,activo=TRUE
            RETURNING id_esquema::text AS id,id_espacio::text AS "idEspacio",nombre,activo
            """,
            (payload["idEspacio"], payload["nombre"]),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def replace_permission_rules(scheme_id: str, items: list[dict]) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM tareas_esquema_reglas WHERE id_esquema=%s", (scheme_id,))
        for item in items:
            cur.execute(
                """
                INSERT INTO tareas_esquema_reglas(
                  id_esquema,accion,tipo_sujeto,id_sujeto,permitir,orden
                ) VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (
                    scheme_id, item["accion"], item["tipoSujeto"], item.get("idSujeto"),
                    item.get("permitir", True), int(item.get("orden", 0)),
                ),
            )
        conn.commit()
    return next((item["reglas"] for item in list_permission_schemes() if item["id"] == scheme_id), [])


def assign_task(key: str, user_id: str | None, actor: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "asignar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema no permite asignar esta tarea.")
        cur.execute("UPDATE tareas SET id_asignado=%s WHERE id_tarea=%s", (user_id, task["id"]))
        _activity(cur, task["id"], actor["id"], "asignacion", "idAsignado", task["idAsignado"], user_id)
        _observar(cur, task["id"], user_id)
        conn.commit()
    _publish_task(task["idEspacio"], {"action": "updated", "key": key.upper()})
    if user_id and user_id != actor["id"]:
        _notificar([user_id], {
            "tareaClave": key.upper(), "titulo": task["titulo"], "motivo": "asignacion",
            "texto": f"{actor.get('nombre') or 'Alguien'} te asignó {key.upper()}.",
        })
    return task_detail(key, actor)


def delete_task(key: str, actor: dict) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "eliminar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema no permite eliminar esta tarea.")
        cur.execute("DELETE FROM tareas WHERE id_tarea=%s", (task["id"],))
        conn.commit()
    _publish_task(task["idEspacio"], {"action": "deleted", "key": key.upper()})


def update_comment(key: str, comment_id: str, payload: dict, actor: dict) -> dict:
    body = str(payload.get("cuerpo") or "").strip()
    if not body:
        raise ValueError("El comentario no puede estar vacío.")
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        cur.execute(
            """
            UPDATE tareas_comentarios SET cuerpo=%s,editado_at=now()
            WHERE id_comentario=%s AND id_tarea=%s AND (id_autor=%s OR %s)
            RETURNING id_comentario::text AS id,cuerpo,editado_at AS "editadoAt"
            """,
            (body, comment_id, task["id"], actor["id"], bool(actor.get("isSuperadmin"))),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("No puedes editar este comentario.")
        conn.commit()
        return row


def delete_comment(key: str, comment_id: str, actor: dict) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        cur.execute(
            "UPDATE tareas_comentarios SET eliminado_at=now() WHERE id_comentario=%s AND id_tarea=%s",
            (comment_id, task["id"]),
        )
        if not cur.rowcount:
            raise ValueError("Comentario no encontrado.")
        conn.commit()


def add_attachment(key: str, payload: dict, actor: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "adjuntar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema no permite adjuntar archivos.")
        cur.execute(
            """
            INSERT INTO tareas_adjuntos(id_tarea,id_media,nombre,metadata,subido_por)
            VALUES(%s,%s,%s,%s,%s)
            RETURNING id_adjunto::text AS id,id_media::text AS "idMedia",nombre,metadata,
                      created_at AS "createdAt"
            """,
            (task["id"], payload["idMedia"], payload.get("nombre"), Jsonb(payload.get("metadata") or {}), actor["id"]),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def delete_attachment(key: str, attachment_id: str, actor: dict) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        task = _task_by_key(cur, key)
        if not task:
            raise ValueError("Tarea no encontrada.")
        if not _schema_allows(cur, "adjuntar", actor, task["idEspacio"], task):
            raise PermissionError("El esquema no permite quitar adjuntos.")
        cur.execute(
            "DELETE FROM tareas_adjuntos WHERE id_adjunto=%s AND id_tarea=%s",
            (attachment_id, task["id"]),
        )
        if not cur.rowcount:
            raise ValueError("Adjunto no encontrado.")
        conn.commit()


def delete_config_item(table: str, id_column: str, item_id: str) -> None:
    allowed = {
        ("tareas_campos", "id_campo"),
        ("tareas_reglas_transicion", "id_regla"),
        ("tareas_esquema_reglas", "id_regla"),
    }
    if (table, id_column) not in allowed:
        raise ValueError("Tipo de configuración no permitido.")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE {id_column}=%s", (item_id,))
        if not cur.rowcount:
            raise ValueError("Elemento no encontrado.")
        conn.commit()


def _publish_task(space_id: str, event: dict) -> None:
    try:
        from .realtime import publicar
        publicar(f"tareas:espacio:{space_id}", {"type": "tarea.evento", **event})
    except Exception:
        pass


def _notificar(user_ids, event: dict) -> None:
    """Aviso personal al canal `usuario:{id}`.

    El canal del espacio sólo lo escucha quien tiene el tablero abierto, así que sin esto
    a quien le asignan trabajo no se entera hasta que entra a mirar.
    """
    destinatarios = {str(uid) for uid in user_ids if uid}
    if not destinatarios:
        return
    try:
        from .realtime import publicar
        for user_id in destinatarios:
            publicar(f"usuario:{user_id}", {"type": "tarea.notificacion", **event})
    except Exception:
        pass


def _observar(cur, task_id: str, user_id: str | None) -> None:
    """Suscribe a una persona a la tarea; quien la trabaja debe seguir su hilo."""
    if not user_id:
        return
    cur.execute(
        "INSERT INTO tareas_observadores(id_tarea,id_usuario) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (task_id, user_id),
    )
