"""Cliente OpenAI-compatible para DeepSeek: streaming para el chat, bloqueante para la ficha."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..config import settings


def completar(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float,
    top_p: float | None = None,
    seed: int | None = None,
    max_tokens: int = 2600,
) -> dict:
    """Una sola respuesta completa, sin streaming.

    La ficha se genera de golpe: no hay nada que mostrar token a token porque el
    documento solo tiene sentido entero. `seed` se envía cuando el proveedor la admite;
    es lo que permite repetir una generación idéntica y sostener el experimento de
    reproducibilidad.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if seed is not None:
        payload["seed"] = seed

    with httpx.Client(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
        respuesta = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if respuesta.status_code >= 400:
        detalle = respuesta.text[:300]
        raise RuntimeError(f"El proveedor respondió {respuesta.status_code}: {detalle}")

    datos = respuesta.json()
    opciones = datos.get("choices") or []
    if not opciones:
        raise RuntimeError("El proveedor no devolvió ninguna respuesta.")
    uso = datos.get("usage") or {}
    return {
        "texto": (opciones[0].get("message") or {}).get("content") or "",
        "tokensEntrada": uso.get("prompt_tokens"),
        "tokensSalida": uso.get("completion_tokens"),
        "modelo": datos.get("model") or model,
    }


async def stream_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    temperature: float,
) -> AsyncIterator[dict]:
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools or None,
        "tool_choice": "auto" if tools else None,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={key: value for key, value in payload.items() if value is not None},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
