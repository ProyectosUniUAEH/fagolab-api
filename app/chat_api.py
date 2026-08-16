"""Endpoints HTTP del chat; el WebSocket únicamente difunde sus resultados."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import repo_chat
from .auth import request_user

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _actor(request: Request) -> dict:
    return request_user(request)


def _error(error: Exception) -> HTTPException:
    if isinstance(error, PermissionError): return HTTPException(403, str(error))
    if isinstance(error, LookupError): return HTTPException(404, str(error))
    return HTTPException(422, str(error))


def _publish(channel: str, event: dict) -> None:
    # Importación diferida: permite que el repositorio y las pruebas no dependan del loop WS.
    from .realtime import publicar
    publicar(channel, event)


@router.get("/conversaciones")
def conversations(request: Request):
    return repo_chat.list_conversations(_actor(request)["id"])


@router.post("/conversaciones", status_code=201)
def direct_conversation(payload: dict, request: Request):
    actor = _actor(request)
    try:
        conversation, created = repo_chat.get_or_create_direct(actor["id"], payload.get("idUsuario"))
    except (ValueError, LookupError, PermissionError) as error:
        raise _error(error)
    if created:
        _publish(f"usuario:{payload.get('idUsuario')}", {"type": "chat.conversation", "conversation": conversation})
    return conversation


@router.post("/grupos", status_code=201)
def group_conversation(payload: dict, request: Request):
    actor = _actor(request)
    try:
        conversation = repo_chat.create_group(actor["id"], payload)
    except (ValueError, LookupError, PermissionError) as error:
        raise _error(error)
    for member in conversation["miembros"]:
        _publish(f"usuario:{member['id']}", {"type": "chat.conversation", "conversation": conversation})
    return conversation


@router.get("/conversaciones/{conversation_id}")
def conversation(conversation_id: str, request: Request):
    try: return repo_chat.get_conversation(conversation_id, _actor(request)["id"])
    except (ValueError, PermissionError) as error: raise _error(error)


@router.get("/conversaciones/{conversation_id}/mensajes")
def messages(conversation_id: str, request: Request, before: str | None = None, limit: int = 50):
    try: return repo_chat.list_messages(conversation_id, _actor(request)["id"], before, limit)
    except (ValueError, PermissionError) as error: raise _error(error)


@router.post("/conversaciones/{conversation_id}/mensajes", status_code=201)
def send_message(conversation_id: str, payload: dict, request: Request):
    actor = _actor(request)
    try: message = repo_chat.send_message(conversation_id, actor["id"], payload)
    except (ValueError, PermissionError) as error: raise _error(error)
    _publish(f"conversacion:{conversation_id}", {"type": "chat.message", "message": message})
    return message


@router.post("/conversaciones/{conversation_id}/leido")
def mark_read(conversation_id: str, payload: dict, request: Request):
    actor = _actor(request)
    try: receipt = repo_chat.mark_read(conversation_id, actor["id"], payload.get("idMensaje"))
    except (ValueError, PermissionError) as error: raise _error(error)
    _publish(f"conversacion:{conversation_id}", {"type": "chat.read", "read": receipt})
    return receipt


@router.patch("/mensajes/{message_id}")
def edit_message(message_id: str, payload: dict, request: Request):
    actor = _actor(request)
    try: message = repo_chat.edit_message(message_id, actor["id"], payload.get("cuerpo"), "chat.messages.moderate" in actor.get("permissions", []))
    except (ValueError, PermissionError) as error: raise _error(error)
    _publish(f"conversacion:{message['idConversacion']}", {"type": "chat.message.updated", "message": message})
    return message


@router.delete("/mensajes/{message_id}")
def delete_message(message_id: str, request: Request):
    actor = _actor(request)
    try: deleted = repo_chat.delete_message(message_id, actor["id"], "chat.messages.moderate" in actor.get("permissions", []))
    except (ValueError, PermissionError) as error: raise _error(error)
    _publish(f"conversacion:{deleted['idConversacion']}", {"type": "chat.message.deleted", "message": deleted})
    return deleted


@router.post("/mensajes/{message_id}/reacciones")
def reaction(message_id: str, payload: dict, request: Request):
    actor = _actor(request)
    try: message = repo_chat.toggle_reaction(message_id, actor["id"], payload.get("emoji"))
    except (ValueError, PermissionError) as error: raise _error(error)
    _publish(f"conversacion:{message['idConversacion']}", {"type": "chat.message.updated", "message": message})
    return message
