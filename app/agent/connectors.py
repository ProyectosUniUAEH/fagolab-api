"""Conectores web seguros: búsqueda, apertura controlada y Crossref."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from ..config import settings


MAX_BYTES = 512 * 1024


def _domain_allowed(host: str, policy: dict) -> bool:
    host = host.lower().rstrip(".")
    blocked = [d.lower().lstrip("*.") for d in policy.get("dominiosBloqueados", [])]
    allowed = [d.lower().lstrip("*.") for d in policy.get("dominiosPermitidos", [])]
    if any(host == d or host.endswith(f".{d}") for d in blocked):
        return False
    return not allowed or any(host == d or host.endswith(f".{d}") for d in allowed)


async def validate_public_url(url: str, policy: dict) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Solo se permiten URLs http/https válidas.")
    if not _domain_allowed(parsed.hostname, policy):
        raise ValueError("El dominio no está permitido por la política.")
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(None, socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    for item in infos:
        address = ipaddress.ip_address(item[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError("La URL resuelve a una red no pública.")
    return url


async def open_url(url: str, policy: dict) -> dict:
    await validate_public_url(url, policy)
    async with httpx.AsyncClient(
        timeout=settings.AGENT_HTTP_TIMEOUT_S,
        follow_redirects=False,
        headers={"User-Agent": "FagoLab-Agent/1.0"},
    ) as client:
        current = url
        for _ in range(4):
            response = await client.get(current)
            if response.is_redirect:
                target = str(response.next_request.url)
                await validate_public_url(target, policy)
                current = target
                continue
            response.raise_for_status()
            raw = response.content[:MAX_BYTES]
            return {
                "url": current,
                "contentType": response.headers.get("content-type", ""),
                "texto": raw.decode(response.encoding or "utf-8", "replace"),
                "truncado": len(response.content) > MAX_BYTES,
            }
    raise ValueError("Demasiadas redirecciones.")


async def resolve_doi(doi: str) -> dict:
    clean = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    async with httpx.AsyncClient(timeout=settings.AGENT_HTTP_TIMEOUT_S) as client:
        response = await client.get(
            f"https://api.crossref.org/works/{clean}",
            headers={"User-Agent": "FagoLab-Agent/1.0 (mailto:admin@fagolab.local)"},
        )
        response.raise_for_status()
        message = response.json()["message"]
        return {
            "doi": message.get("DOI"),
            "titulo": (message.get("title") or [""])[0],
            "autores": [
                " ".join(filter(None, [item.get("given"), item.get("family")]))
                for item in message.get("author", [])
            ],
            "publicado": (message.get("published") or {}).get("date-parts", [[]])[0],
            "url": message.get("URL"),
            "editorial": message.get("publisher"),
        }


async def brave_search(query: str, limit: int, api_key: str, base_url: str) -> list[dict]:
    if not api_key:
        raise ValueError("El conector Brave no tiene llave configurada.")
    async with httpx.AsyncClient(timeout=settings.AGENT_HTTP_TIMEOUT_S) as client:
        endpoint = base_url.rstrip("/") or "https://api.search.brave.com"
        if not endpoint.endswith("/res/v1/web/search"):
            endpoint = f"{endpoint}/res/v1/web/search"
        response = await client.get(
            endpoint,
            params={"q": query, "count": min(max(limit, 1), 10)},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
        response.raise_for_status()
        return [
            {
                "titulo": row.get("title"),
                "url": row.get("url"),
                "fragmento": row.get("description"),
                "fecha": row.get("age"),
            }
            for row in response.json().get("web", {}).get("results", [])
        ]
