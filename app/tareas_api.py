"""API de tareas. Las rutas literales preceden a las paramétricas."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from .auth import request_user
from . import repo_auth, repo_tareas
from .tareas_workflow import public_catalog


router = APIRouter(prefix="/api/tareas", tags=["tareas"])


def actor(request: Request) -> dict:
    user = request_user(request)
    user.setdefault("permissions", getattr(request.state, "user", {}).get("permissions", []))
    user.setdefault("roles", repo_auth.user_roles(user["id"]))
    user.setdefault("groups", repo_auth.user_groups(user["id"]))
    return user


def _value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc), headers={"X-Error-Code": "validation_error"})


@router.get("/espacios")
def espacios():
    return repo_tareas.list_spaces()


@router.post("/espacios", status_code=201)
def crear_espacio(payload: dict):
    try:
        return repo_tareas.create_space(payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.patch("/espacios/{space_id}")
def actualizar_espacio(space_id: str, payload: dict):
    try:
        return repo_tareas.update_space(space_id, payload)
    except ValueError as exc:
        raise _value_error(exc)


@router.get("/tablero")
def tablero(request: Request, idEspacio: str | None = None):
    return repo_tareas.board(actor(request), idEspacio)


@router.get("/actividad")
def actividad(idEspacio: str | None = None, limit: int = Query(100, ge=1, le=500)):
    return repo_tareas.activity(idEspacio, limit)


@router.get("/config")
def configuracion():
    return repo_tareas.config_snapshot()


@router.get("/config/tipos-regla")
def tipos_regla():
    return public_catalog()


@router.get("/config/campos")
def campos():
    return repo_tareas.config_snapshot()["campos"]


@router.post("/config/campos", status_code=201)
def crear_campo(payload: dict):
    try:
        return repo_tareas.create_field(payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.delete("/config/campos/{item_id}", status_code=204)
def eliminar_campo(item_id: str):
    try:
        repo_tareas.delete_config_item("tareas_campos", "id_campo", item_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/config/reglas", status_code=201)
def crear_regla(payload: dict):
    try:
        return repo_tareas.create_transition_rule(payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.patch("/config/reglas/{item_id}")
def actualizar_regla(item_id: str, payload: dict):
    try:
        return repo_tareas.update_transition_rule(item_id, payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.delete("/config/reglas/{item_id}", status_code=204)
def eliminar_regla(item_id: str):
    try:
        repo_tareas.delete_config_item("tareas_reglas_transicion", "id_regla", item_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/config/tipos")
def tipos():
    return repo_tareas.list_types()


@router.post("/config/tipos", status_code=201)
def crear_tipo(payload: dict):
    try:
        return repo_tareas.create_type(payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.patch("/config/tipos/{type_id}")
def actualizar_tipo(type_id: str, payload: dict):
    try:
        return repo_tareas.update_type(type_id, payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.delete("/config/tipos/{type_id}", status_code=204)
def eliminar_tipo(type_id: str):
    try:
        repo_tareas.delete_type(type_id)
    except ValueError as exc:
        raise _value_error(exc)


@router.get("/config/flujos")
def flujos():
    return repo_tareas.config_snapshot()


@router.post("/config/flujos", status_code=201)
def crear_flujo(payload: dict):
    try:
        return repo_tareas.create_flow(payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.patch("/config/flujos/{flow_id}")
def actualizar_flujo(flow_id: str, payload: dict):
    try:
        return repo_tareas.update_flow(flow_id, payload)
    except ValueError as exc:
        raise _value_error(exc)


@router.put("/config/flujos/{flow_id}/estados")
def guardar_estados(flow_id: str, payload: dict):
    try:
        return repo_tareas.replace_flow_states(flow_id, payload.get("estados") or [])
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.put("/config/flujos/{flow_id}/transiciones")
def guardar_transiciones(flow_id: str, payload: dict):
    try:
        return repo_tareas.replace_flow_transitions(flow_id, payload.get("transiciones") or [])
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.get("/config/esquemas-permisos")
def esquemas_permisos():
    return repo_tareas.list_permission_schemes()


@router.post("/config/esquemas-permisos", status_code=201)
def crear_esquema_permisos(payload: dict):
    try:
        return repo_tareas.create_permission_scheme(payload)
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.put("/config/esquemas-permisos/{scheme_id}/reglas")
def guardar_reglas_permisos(scheme_id: str, payload: dict):
    try:
        return repo_tareas.replace_permission_rules(scheme_id, payload.get("reglas") or [])
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.get("/progreso")
def progreso(idEspacio: str | None = None):
    """Carga y avance por persona, para la pestaña de seguimiento del equipo."""
    return repo_tareas.progress_by_person(idEspacio)


@router.get("/objetos")
def objetos(q: str | None = None, limite: int = 20):
    """Selector de vínculo: busca cajas, peces, viales… por código."""
    return repo_tareas.buscar_objetos(q or "", limite)


@router.get("/objeto/{codigo}")
def tareas_por_objeto(codigo: str, request: Request):
    """Trazabilidad inversa: qué trabajo cuelga de un registro del experimento."""
    try:
        return repo_tareas.tareas_de_objeto(codigo, actor(request))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("")
def listar(
    request: Request,
    q: str | None = None,
    estado: str | None = None,
    idEspacio: str | None = None,
    asignado: str | None = None,
):
    return repo_tareas.list_tasks(
        actor(request),
        {"q": q, "estado": estado, "idEspacio": idEspacio, "asignado": asignado},
    )


@router.post("", status_code=201)
def crear(payload: dict, request: Request):
    try:
        return repo_tareas.create_task(payload, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise _value_error(exc)


@router.get("/{clave}")
def detalle(clave: str, request: Request):
    try:
        return repo_tareas.task_detail(clave, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.patch("/{clave}")
def actualizar(clave: str, payload: dict, request: Request):
    try:
        return repo_tareas.update_task(clave, payload, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise _value_error(exc)


@router.delete("/{clave}", status_code=204)
def eliminar(clave: str, request: Request):
    try:
        repo_tareas.delete_task(clave, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.put("/{clave}/asignado")
def asignar(clave: str, payload: dict, request: Request):
    try:
        return repo_tareas.assign_task(clave, payload.get("idUsuario"), actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{clave}/transiciones")
def transiciones(clave: str, request: Request):
    try:
        return repo_tareas.legal_transitions(clave, actor(request))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{clave}/transiciones/{id_transicion}")
def transicionar(clave: str, id_transicion: str, payload: dict, request: Request):
    try:
        return repo_tareas.transition_task(clave, id_transicion, payload, actor(request))
    except PermissionError as exc:
        # El motor ya explica qué condición falla; reemplazarlo por un texto genérico
        # dejaba a quien pulsa sin saber qué resolver.
        raise HTTPException(
            403,
            str(exc) or "La transición no está disponible.",
            headers={"X-Error-Code": "transicion_no_disponible"},
        )
    except ValueError as exc:
        raise _value_error(exc)


@router.post("/{clave}/comentarios", status_code=201)
def comentar(clave: str, payload: dict, request: Request):
    try:
        return repo_tareas.add_comment(clave, payload, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise _value_error(exc)


@router.patch("/{clave}/comentarios/{comment_id}")
def editar_comentario(clave: str, comment_id: str, payload: dict, request: Request):
    try:
        return repo_tareas.update_comment(clave, comment_id, payload, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise _value_error(exc)


@router.delete("/{clave}/comentarios/{comment_id}", status_code=204)
def eliminar_comentario(clave: str, comment_id: str, request: Request):
    try:
        repo_tareas.delete_comment(clave, comment_id, actor(request))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{clave}/adjuntos", status_code=201)
def adjuntar(clave: str, payload: dict, request: Request):
    try:
        return repo_tareas.add_attachment(clave, payload, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except (ValueError, KeyError) as exc:
        raise _value_error(ValueError(str(exc)))


@router.delete("/{clave}/adjuntos/{attachment_id}", status_code=204)
def eliminar_adjunto(clave: str, attachment_id: str, request: Request):
    try:
        repo_tareas.delete_attachment(clave, attachment_id, actor(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
