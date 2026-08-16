"""API del agente IA; nunca expone secretos descifrados al navegador."""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request

from . import repo_ia
from .agent.loop import cancel_run, resolve_approval, start_run
from .auth import request_user
from .config import settings


router = APIRouter(prefix="/api/ia", tags=["agente-ia"])


def actor(request: Request) -> dict:
    return request_user(request)


@router.get("/configuracion")
def configuracion():
    return repo_ia.get_config()


@router.put("/configuracion")
def guardar_configuracion(payload: dict, request: Request):
    return repo_ia.save_config(payload, actor(request)["id"])


@router.post("/configuracion/probar")
async def probar_configuracion():
    try:
        config = await asyncio.to_thread(repo_ia.get_config, True)
        if not config.get("apiKey"):
            raise ValueError("No hay una llave configurada.")
        async with httpx.AsyncClient(timeout=settings.AGENT_HTTP_TIMEOUT_S) as client:
            response = await client.get(
                f"{config['baseUrl'].rstrip('/')}/models",
                headers={"Authorization": f"Bearer {config['apiKey']}"},
            )
            response.raise_for_status()
        await asyncio.to_thread(repo_ia.mark_verification, True, "Conexión correcta.")
        return {"ok": True, "detalle": "Conexión correcta."}
    except Exception as exc:
        await asyncio.to_thread(repo_ia.mark_verification, False, str(exc))
        raise HTTPException(422, f"No se pudo verificar el proveedor: {exc}")


@router.get("/politicas")
def politicas():
    policy = repo_ia.active_policy()
    return [policy] if policy else []


@router.put("/politicas/{policy_id}")
def guardar_politica(policy_id: str, payload: dict):
    try:
        return repo_ia.save_policy(policy_id, payload)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/conectores")
def conectores():
    return repo_ia.connectors()


@router.post("/conectores", status_code=201)
def crear_conector(payload: dict):
    try:
        return repo_ia.create_connector(payload)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc))


@router.patch("/conectores/{connector_id}")
def guardar_conector(connector_id: str, payload: dict):
    try:
        return repo_ia.save_connector(connector_id, payload)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/uso")
def uso():
    return repo_ia.usage_summary()


@router.get("/conversaciones")
def conversaciones(request: Request):
    return repo_ia.list_conversations(actor(request)["id"])


@router.post("/conversaciones", status_code=201)
def crear_conversacion(payload: dict, request: Request):
    mode = payload.get("modo", "ask")
    user = actor(request)
    if mode not in {"ask", "agente", "super"}:
        raise HTTPException(422, "Modo inválido.")
    if mode == "agente" and not user.get("isSuperadmin") and "ia.agent.act" not in user.get("permissions", []):
        raise HTTPException(403, "No tienes permiso para el modo Agente.")
    if mode == "super" and (
        not settings.AGENT_SHELL_ENABLED
        or not user.get("isSuperadmin")
        or "ia.shell.execute" not in user.get("permissions", [])
    ):
        raise HTTPException(403, "El modo Super no está disponible.")
    return repo_ia.create_conversation(user["id"], payload)


@router.get("/conversaciones/{conversation_id}")
def detalle_conversacion(conversation_id: str, request: Request):
    try:
        return repo_ia.conversation_detail(conversation_id, actor(request)["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/conversaciones/{conversation_id}", status_code=204)
def eliminar_conversacion(conversation_id: str, request: Request):
    try:
        repo_ia.delete_conversation(conversation_id, actor(request)["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/conversaciones/{conversation_id}/mensajes", status_code=202)
async def enviar_mensaje(conversation_id: str, payload: dict, request: Request):
    user = actor(request)
    content = str(payload.get("contenido") or "").strip()
    if not content:
        raise HTTPException(422, "El mensaje no puede estar vacío.")
    try:
        detail = await asyncio.to_thread(repo_ia.conversation_detail, conversation_id, user["id"])
        mode = detail["modo"]
        if mode in {"agente", "super"} and not user.get("isSuperadmin") and "ia.agent.act" not in user.get("permissions", []):
            raise HTTPException(403, "No tienes permiso para ejecutar acciones.")
        await asyncio.to_thread(repo_ia.add_message, conversation_id, "user", content)
        run = await asyncio.to_thread(repo_ia.create_run, conversation_id, user["id"], mode)
        start_run(run, conversation_id, user)
        return {"ok": True, "runId": run["id"]}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/conversaciones/{conversation_id}/cancelar")
def cancelar(conversation_id: str, payload: dict, request: Request):
    # La lectura previa evita cancelar ejecuciones de otra persona.
    try:
        repo_ia.conversation_detail(conversation_id, actor(request)["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    run_id = str(payload.get("runId") or "")
    if not cancel_run(run_id):
        raise HTTPException(409, "La ejecución ya terminó o no existe.")
    return {"ok": True}


@router.put("/conversaciones/{conversation_id}/shell")
def habilitar_shell(conversation_id: str, payload: dict, request: Request):
    user = actor(request)
    if not settings.AGENT_SHELL_ENABLED:
        raise HTTPException(403, "El interruptor global del shell está apagado.")
    if settings.ENV.lower() not in {"local", "dev", "development"} and not settings.AGENT_SHELL_ALLOW_REMOTE:
        raise HTTPException(403, "El shell está deshabilitado fuera del entorno local.")
    if not user.get("isSuperadmin"):
        raise HTTPException(403, "Solo una superadministradora puede habilitar el shell.")
    try:
        return repo_ia.set_shell(
            conversation_id,
            user["id"],
            bool(payload.get("habilitado")),
            str(payload.get("confirmacion") or ""),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _resolver(call_id: str, approved: bool, request: Request) -> dict:
    # La fila se marca sólo si pertenece a quien responde; después se despierta el loop.
    try:
        row = repo_ia.resolve_tool_call(call_id, approved, actor(request)["id"])
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not resolve_approval(call_id, approved):
        # El loop ya no espera (expiró o se canceló): se devuelve la fila a su estado previo
        # para que la interfaz no muestre una propuesta "aprobada" que nunca se ejecutó.
        repo_ia.complete_tool_call(call_id, "expirada", error="La ejecución ya no esperaba esta respuesta.")
        raise HTTPException(409, "La ejecución ya no espera esta aprobación.")
    return row


@router.post("/llamadas/{call_id}/aprobar")
def aprobar(call_id: str, request: Request):
    return _resolver(call_id, True, request)


@router.post("/llamadas/{call_id}/rechazar")
def rechazar(call_id: str, request: Request):
    return _resolver(call_id, False, request)
