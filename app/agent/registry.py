"""Registro y filtrado fail-closed de herramientas del agente."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..config import settings


Handler = Callable[[dict, dict], Awaitable[Any]]
LEVELS = {"ask": 0, "agente": 1, "super": 2}


@dataclass(frozen=True)
class Tool:
    nombre: str
    descripcion: str
    parametros: dict
    permiso: str
    mutante: bool
    nivel: str
    riesgo: str
    handler: Handler

    def deepseek(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.nombre,
                "description": self.descripcion,
                "parameters": self.parametros,
            },
        }


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.nombre in TOOLS:
        raise RuntimeError(f"Herramienta duplicada: {tool.nombre}")
    TOOLS[tool.nombre] = tool
    return tool


def available(ctx: dict, mode: str, policy: dict | None = None) -> list[Tool]:
    permissions = set(ctx.get("permissions") or [])
    enabled = set((policy or {}).get("herramientasHabilitadas") or [])
    result = []
    for item in TOOLS.values():
        if LEVELS[item.nivel] > LEVELS.get(mode, 0):
            continue
        if item.permiso and not ctx.get("isSuperadmin") and item.permiso not in permissions:
            continue
        if enabled and item.nombre not in enabled and not any(
            item.nombre.startswith(f"{prefix}.") for prefix in enabled
        ):
            continue
        if item.nombre == "sistema.ejecutar_comando" and not settings.AGENT_SHELL_ENABLED:
            continue
        result.append(item)
    return sorted(result, key=lambda item: item.nombre)


def assert_available(name: str, ctx: dict, mode: str, policy: dict | None = None) -> Tool:
    allowed = {tool.nombre: tool for tool in available(ctx, mode, policy)}
    if name not in allowed:
        raise PermissionError("permiso_denegado")
    return allowed[name]
