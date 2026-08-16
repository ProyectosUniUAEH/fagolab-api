"""WebSocket autenticado, presencia y bus de eventos de colaboración.

El bus actual vive en memoria, por lo que el despliegue debe conservar ``--workers 1``.
Sus consumidores dependen solo de :class:`RealtimeBus`; una futura implementación
PostgreSQL LISTEN/NOTIFY puede sustituirlo sin cambiar el contrato del navegador.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Awaitable, Callable, Protocol
import uuid

import jwt
from fastapi import WebSocket, WebSocketDisconnect

from . import repo_auth
from .auth import ACCESS_COOKIE, decode_access_token
from .config import settings
from .db import get_conn


LOCAL_ORIGIN = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+|100\.\d+\.\d+\.\d+)(:\d+)?$",
    re.IGNORECASE,
)
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}$")
TYPING_INTERVAL_SECONDS = 3.0
Delivery = Callable[[dict], Awaitable[None]]


class RealtimeBus(Protocol):
    """Abstracción de publicación y suscripción, independiente del transporte."""

    async def publish(self, channel: str, event: dict) -> None: ...
    async def subscribe(self, connection_id: str, channel: str, deliver: Delivery) -> None: ...
    async def unsubscribe(self, connection_id: str, channel: str) -> None: ...
    async def drop_connection(self, connection_id: str) -> None: ...


class InMemoryBus:
    """Bus para una sola instancia; no comparte eventos entre procesos."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, dict[str, Delivery]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, event: dict) -> None:
        async with self._lock:
            subscribers = list(self._subscriptions.get(channel, {}).items())
        stale: list[str] = []
        for connection_id, deliver in subscribers:
            try:
                await deliver(event)
            except Exception:
                stale.append(connection_id)
        for connection_id in stale:
            await self.drop_connection(connection_id)

    async def subscribe(self, connection_id: str, channel: str, deliver: Delivery) -> None:
        async with self._lock:
            self._subscriptions[channel][connection_id] = deliver

    async def unsubscribe(self, connection_id: str, channel: str) -> None:
        async with self._lock:
            members = self._subscriptions.get(channel)
            if not members:
                return
            members.pop(connection_id, None)
            if not members:
                self._subscriptions.pop(channel, None)

    async def drop_connection(self, connection_id: str) -> None:
        async with self._lock:
            for channel in list(self._subscriptions):
                self._subscriptions[channel].pop(connection_id, None)
                if not self._subscriptions[channel]:
                    self._subscriptions.pop(channel, None)


realtime_bus: RealtimeBus = InMemoryBus()
_event_loop: asyncio.AbstractEventLoop | None = None


def capture_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Registra el loop ASGI para que repositorios HTTP síncronos puedan publicar."""
    global _event_loop
    _event_loop = loop


def publicar(canal: str, evento: dict):
    """Publica desde handlers síncronos de HTTP de manera segura para hilos.

    El evento siempre recibe su canal, para que los stores puedan compartir socket y
    despachar sin inferirlo por el tipo. Devuelve el ``Future`` para quien necesite
    observar un error; las rutas HTTP normalmente no deben esperar el broadcast.
    """
    if not _event_loop or _event_loop.is_closed():
        raise RuntimeError("El bus realtime no está inicializado.")
    payload = {**evento, "channel": canal}
    return asyncio.run_coroutine_threadsafe(realtime_bus.publish(canal, payload), _event_loop)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def websocket_origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    normalized = origin.rstrip("/")
    configured = {item.strip().rstrip("/") for item in settings.CORS_ORIGINS if item.strip()}
    return normalized in configured or bool(LOCAL_ORIGIN.fullmatch(normalized))


@dataclass
class PresenceConnection:
    id: str
    websocket: WebSocket
    user_id: str
    session_id: str
    user_name: str
    cargo: str | None
    avatar_uri: str | None
    role: str
    is_superadmin: bool
    permissions: frozenset[str]
    connected_at: datetime
    last_seen_at: datetime
    last_typing_at: dict[str, float]


class PresenceHub:
    def __init__(self) -> None:
        self._connections: dict[str, PresenceConnection] = {}
        self._lock = asyncio.Lock()

    async def add(self, connection: PresenceConnection) -> None:
        async with self._lock:
            self._connections[connection.id] = connection

    async def remove(self, connection_id: str) -> None:
        async with self._lock:
            self._connections.pop(connection_id, None)

    async def heartbeat(self, connection_id: str) -> None:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection:
                connection.last_seen_at = utcnow()

    async def snapshot(self) -> list[dict]:
        async with self._lock:
            connections = list(self._connections.values())
        users: dict[str, dict] = {}
        for connection in connections:
            item = users.setdefault(connection.user_id, {"id": connection.user_id, "nombre": connection.user_name,
                "cargo": connection.cargo, "avatarUri": connection.avatar_uri, "rol": connection.role,
                "connectedAt": connection.connected_at, "lastSeenAt": connection.last_seen_at, "connections": 0})
            item["connections"] += 1
            item["connectedAt"] = min(item["connectedAt"], connection.connected_at)
            item["lastSeenAt"] = max(item["lastSeenAt"], connection.last_seen_at)
        return [{**item, "connectedAt": item["connectedAt"].isoformat(), "lastSeenAt": item["lastSeenAt"].isoformat()}
                for item in sorted(users.values(), key=lambda row: row["nombre"].lower())]


presence_hub = PresenceHub()


def _has_row(sql: str, params: tuple[str, ...]) -> bool:
    """Consulta mínima y fail-closed para autorizar un canal contra la BD."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() is not None


def _authorize_channel_db(channel: str, user_id: str, permissions: frozenset[str], is_superadmin: bool) -> bool:
    """Nunca confía en la cadena enviada por el cliente: valida tipo, ACL y datos."""
    if channel == "presencia":
        return is_superadmin or "security.presence.view" in permissions
    if channel == f"usuario:{user_id}":
        return True
    if channel.startswith("tareas:espacio:"):
        space_id = channel.removeprefix("tareas:espacio:")
        if not UUID_RE.fullmatch(space_id) or not (is_superadmin or "tareas.items.view" in permissions):
            return False
        return _has_row("SELECT 1 FROM tareas_espacios WHERE id_espacio=%s", (space_id,))
    if channel.startswith("ia:conversacion:"):
        conversation_id = channel.removeprefix("ia:conversacion:")
        if not UUID_RE.fullmatch(conversation_id) or not (is_superadmin or "ia.agent.ask" in permissions):
            return False
        return _has_row("""
            SELECT 1 FROM ia_conversaciones WHERE id_conversacion=%s AND id_usuario=%s
        """, (conversation_id, user_id))
    prefix, separator, target_id = channel.partition(":")
    if not separator or not UUID_RE.fullmatch(target_id):
        return False
    if prefix == "conversacion":
        if not (is_superadmin or "chat.conversations.view" in permissions):
            return False
        return _has_row("""
            SELECT 1 FROM conversacion_miembros
            WHERE id_conversacion=%s AND id_usuario=%s AND salido_at IS NULL
        """, (target_id, user_id))
    return False


async def authorize_channel(channel: str, connection: PresenceConnection) -> bool:
    try:
        return await asyncio.to_thread(
            _authorize_channel_db, channel, connection.user_id, connection.permissions,
            connection.is_superadmin,
        )
    except Exception:
        # Si una migración no está instalada o falla la BD, no se filtra información.
        return False


async def _authenticate(websocket: WebSocket) -> tuple[dict, dict] | None:
    if not websocket_origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403, reason="Origen no permitido")
        return None
    token = websocket.cookies.get(ACCESS_COOKIE)
    if not token:
        await websocket.close(code=4401, reason="Sesión requerida")
        return None
    try:
        claims = decode_access_token(token)
        if claims.get("type") != "access":
            raise jwt.InvalidTokenError("Tipo de token inválido.")
        user = await asyncio.to_thread(repo_auth.get_user_by_id, claims["sub"])
        session = await asyncio.to_thread(repo_auth.get_active_session, claims["sid"], claims["sub"])
        if not user or not session or not user.get("activo") or user.get("status") != "activa":
            raise jwt.InvalidTokenError("Sesión inactiva.")
    except (jwt.PyJWTError, KeyError, ValueError):
        await websocket.close(code=4401, reason="Sesión no válida")
        return None
    return user, claims


async def _send(connection: PresenceConnection, event: dict) -> None:
    await connection.websocket.send_json(event)


async def _subscribe(connection: PresenceConnection, channel: str, *, automatic: bool = False) -> bool:
    if not await authorize_channel(channel, connection):
        if not automatic:
            await _send(connection, {"type": "realtime.error", "code": "channel_forbidden", "channel": channel})
        return False
    await realtime_bus.subscribe(connection.id, channel, lambda event: _send(connection, event))
    if not automatic:
        await _send(connection, {"type": "channel.subscribed", "channel": channel})
    return True


async def _unsubscribe(connection: PresenceConnection, channel: str) -> None:
    # The private user feed is mandatory for delivery of notifications.
    if channel == f"usuario:{connection.user_id}":
        await _send(connection, {"type": "realtime.error", "code": "channel_required", "channel": channel})
        return
    await realtime_bus.unsubscribe(connection.id, channel)
    await _send(connection, {"type": "channel.unsubscribed", "channel": channel})


async def handle_presence_socket(websocket: WebSocket) -> None:
    authenticated = await _authenticate(websocket)
    if not authenticated:
        return
    user, claims = authenticated
    permissions = frozenset(await asyncio.to_thread(repo_auth.effective_permissions, user["id"], bool(user.get("isSuperadmin"))))
    role = await asyncio.to_thread(repo_auth.primary_role_name, user["id"], bool(user.get("isSuperadmin")))
    now = utcnow()
    connection = PresenceConnection(str(uuid.uuid4()), websocket, user["id"], claims["sid"], user["nombre"],
        user.get("cargo"), user.get("avatarUri"), role, bool(user.get("isSuperadmin")), permissions, now, now, {})

    await websocket.accept()
    await presence_hub.add(connection)
    await _subscribe(connection, f"usuario:{connection.user_id}", automatic=True)
    await _subscribe(connection, "presencia", automatic=True)
    await _send(connection, {"type": "presence.ready", "connectionId": connection.id,
        "canViewRoster": connection.is_superadmin or "security.presence.view" in permissions})
    if connection.is_superadmin or "security.presence.view" in permissions:
        await _send(connection, {"type": "presence.snapshot", "users": await presence_hub.snapshot()})
        await realtime_bus.publish("presencia", {"type": "presence.snapshot", "channel": "presencia", "users": await presence_hub.snapshot()})

    try:
        while True:
            message = await websocket.receive_json()
            active = await asyncio.to_thread(repo_auth.get_active_session, connection.session_id, connection.user_id)
            if not active:
                await websocket.close(code=4401, reason="Sesión finalizada")
                break
            event_type = message.get("type")
            if event_type == "presence.heartbeat":
                await presence_hub.heartbeat(connection.id)
                await asyncio.to_thread(repo_auth.touch_user, connection.user_id)
                await _send(connection, {"type": "presence.pong", "at": utcnow().isoformat()})
            elif event_type == "channel.subscribe" and isinstance(message.get("channel"), str):
                await _subscribe(connection, message["channel"])
            elif event_type == "channel.unsubscribe" and isinstance(message.get("channel"), str):
                await _unsubscribe(connection, message["channel"])
            elif event_type == "chat.typing" and isinstance(message.get("channel"), str):
                channel = message["channel"]
                if not channel.startswith("conversacion:") or not await authorize_channel(channel, connection):
                    await _send(connection, {"type": "realtime.error", "code": "channel_forbidden", "channel": channel})
                    continue
                now_monotonic = asyncio.get_running_loop().time()
                if now_monotonic - connection.last_typing_at.get(channel, 0.0) >= TYPING_INTERVAL_SECONDS:
                    connection.last_typing_at[channel] = now_monotonic
                    await realtime_bus.publish(channel, {"type": "chat.typing", "channel": channel,
                        "userId": connection.user_id, "typing": bool(message.get("typing", True))})
            elif event_type == "chat.read" and isinstance(message.get("channel"), str):
                channel = message["channel"]
                if channel.startswith("conversacion:") and await authorize_channel(channel, connection):
                    await realtime_bus.publish(channel, {"type": "chat.read", "channel": channel,
                        "userId": connection.user_id, "messageId": message.get("messageId")})
                else:
                    await _send(connection, {"type": "realtime.error", "code": "channel_forbidden", "channel": channel})
            else:
                await _send(connection, {"type": "realtime.error", "code": "unsupported_event", "message": "Evento no reconocido."})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await realtime_bus.drop_connection(connection.id)
        await presence_hub.remove(connection.id)
        if connection.is_superadmin or "security.presence.view" in permissions:
            await realtime_bus.publish("presencia", {"type": "presence.snapshot", "channel": "presencia", "users": await presence_hub.snapshot()})
