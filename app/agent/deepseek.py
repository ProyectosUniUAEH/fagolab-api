"""Cliente OpenAI-compatible para DeepSeek con streaming."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..config import settings


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
