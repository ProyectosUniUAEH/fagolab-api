"""Herramientas del agente reutilizando los repositorios de la API."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from .connectors import brave_search, open_url, resolve_doi
from .registry import Tool, register
from .shell import execute as execute_shell
from .. import repo, repo_auth, repo_biblioteca
from ..crypto import decrypt_secret


EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

# Presupuesto por respuesta de herramienta. El experimento ya tiene ~1 280 cajas: devolver
# la tabla entera son ~107 000 tokens, más que la ventana completa del modelo, así que la
# llamada fallaba siempre. Se acota por número de filas y además por tamaño serializado.
LIMITE_FILAS_POR_DEFECTO = 20
LIMITE_FILAS_MAXIMO = 100
PRESUPUESTO_CARACTERES = 24_000

CONSULTA = {
    "type": "object",
    "properties": {
        "q": {"type": "string", "description": "Filtro de texto libre; busca en todos los campos de la fila."},
        "limite": {
            "type": "integer", "minimum": 1, "maximum": LIMITE_FILAS_MAXIMO,
            "description": f"Filas a devolver (máximo {LIMITE_FILAS_MAXIMO}, por defecto {LIMITE_FILAS_POR_DEFECTO}).",
        },
    },
    "additionalProperties": False,
}


async def _thread(fn: Callable, *args) -> Any:
    return await asyncio.to_thread(fn, *args)


def _simple(fn: Callable) -> Callable:
    async def handler(_args: dict, _ctx: dict) -> Any:
        return await _thread(fn)
    return handler


def _coincide(fila: Any, termino: str) -> bool:
    return termino in json.dumps(fila, default=str, ensure_ascii=False).lower()


def _acotar(filas: list, args: dict) -> dict:
    """Filtra, recorta y explica el recorte para que el modelo sepa que hay más datos."""
    termino = str(args.get("q") or "").strip().lower()
    coincidencias = [f for f in filas if _coincide(f, termino)] if termino else filas
    limite = max(1, min(int(args.get("limite") or LIMITE_FILAS_POR_DEFECTO), LIMITE_FILAS_MAXIMO))
    pagina = coincidencias[:limite]
    # Segundo tope: filas muy anchas pueden desbordar aunque sean pocas.
    while pagina and len(json.dumps(pagina, default=str, ensure_ascii=False)) > PRESUPUESTO_CARACTERES:
        pagina = pagina[:-1]
    resultado = {
        "total": len(filas),
        "coincidencias": len(coincidencias),
        "mostradas": len(pagina),
        "filas": pagina,
    }
    if len(pagina) < len(coincidencias):
        resultado["nota"] = (
            f"Se muestran {len(pagina)} de {len(coincidencias)} filas. "
            "Afina el parámetro 'q' o sube 'limite' para ver otras; no supongas nada del resto."
        )
    return resultado


def _dataset(fn: Callable) -> Callable:
    async def handler(args: dict, _ctx: dict) -> Any:
        return _acotar(await _thread(fn), args or {})
    return handler


register(Tool("lab.dashboard", "Resumen y KPIs reales del laboratorio.", EMPTY, "dashboard.main.view", False, "ask", "bajo", _simple(repo.dashboard)))

for spec in (
    ("lab.peces", "Peces registrados. Filtra con 'q' (código, especie, lote).", "peces.records.view", repo.list_peces),
    ("lab.cajas", "Cajas Petri y sus observaciones de colonia. Filtra con 'q' (código, medio, órgano).", "cajas.records.view", repo.list_cajas),
    ("lab.subcultivos", "Subcultivos y pureza. Filtra con 'q'.", "subcultivos.records.view", repo.list_subcultivos),
    ("lab.nanodrop", "Lecturas NanoDrop y su calidad. Filtra con 'q'.", "nanodrop.readings.view", repo.list_nanodrop),
    ("lab.pcr", "Registros y corridas PCR. Filtra con 'q'.", "pcr.runs.view", repo.list_pcr),
    ("lab.geles", "Geles de electroforesis. Filtra con 'q'.", "electroforesis.gels.view", repo.list_geles),
    ("lab.biblioteca", "Documentos de la biblioteca científica. Filtra con 'q'.", "biblioteca.documents.view", repo_biblioteca.listar),
    ("lab.reportes", "Filas consolidadas del reporte experimental. Filtra con 'q'.", "reportes.analytics.view", repo.reporte_rows),
    ("sistema.usuarios", "Directorio de usuarios. Filtra con 'q'.", "security.users.view", repo_auth.list_directory_users),
    ("sistema.auditoria", "Eventos recientes de auditoría. Filtra con 'q'.", "security.audit.view", repo_auth.list_audit),
):
    register(Tool(spec[0], spec[1], CONSULTA, spec[2], False, "ask", "bajo", _dataset(spec[3])))


async def _task_search(args: dict, ctx: dict) -> Any:
    from .. import repo_tareas
    return await _thread(repo_tareas.list_tasks, ctx, args)


async def _task_detail(args: dict, ctx: dict) -> Any:
    from .. import repo_tareas
    return await _thread(repo_tareas.task_detail, args["clave"], ctx)


async def _task_create(args: dict, ctx: dict) -> Any:
    from .. import repo_tareas
    return await _thread(repo_tareas.create_task, args, ctx)


async def _task_update(args: dict, ctx: dict) -> Any:
    from .. import repo_tareas
    payload = dict(args)
    key = payload.pop("clave")
    return await _thread(repo_tareas.update_task, key, payload, ctx)


async def _task_comment(args: dict, ctx: dict) -> Any:
    from .. import repo_tareas
    return await _thread(repo_tareas.add_comment, args["clave"], {"cuerpo": args["cuerpo"]}, ctx)


TASK_KEY = {"type": "object", "required": ["clave"], "properties": {"clave": {"type": "string"}}}
register(Tool("tareas.buscar", "Busca y filtra tareas.", {"type": "object", "properties": {"q": {"type": "string"}, "estado": {"type": "string"}}}, "tareas.items.view", False, "ask", "bajo", _task_search))
register(Tool("tareas.detalle", "Consulta una tarea por clave.", TASK_KEY, "tareas.items.view", False, "ask", "bajo", _task_detail))
register(Tool("tareas.crear", "Crea una tarea; siempre requiere confirmación.", {"type": "object", "required": ["titulo"], "properties": {"titulo": {"type": "string"}, "descripcion": {"type": "string"}, "prioridad": {"type": "string"}, "idAsignado": {"type": "string"}}}, "tareas.items.create", True, "agente", "medio", _task_create))
register(Tool("tareas.actualizar", "Actualiza una tarea; requiere confirmación.", {"type": "object", "required": ["clave"], "properties": {"clave": {"type": "string"}, "titulo": {"type": "string"}, "descripcion": {"type": "string"}, "prioridad": {"type": "string"}, "idAsignado": {"type": "string"}}}, "tareas.items.update", True, "agente", "medio", _task_update))
register(Tool("tareas.comentar", "Agrega un comentario a una tarea.", {"type": "object", "required": ["clave", "cuerpo"], "properties": {"clave": {"type": "string"}, "cuerpo": {"type": "string"}}}, "tareas.comments.create", True, "agente", "medio", _task_comment))


async def _web_open(args: dict, ctx: dict) -> Any:
    return await open_url(args["url"], ctx.get("policy") or {})


async def _doi(args: dict, _ctx: dict) -> Any:
    return await resolve_doi(args["doi"])


async def _search(args: dict, ctx: dict) -> Any:
    connector = ctx.get("connectors", {}).get("brave") or {}
    return await brave_search(
        args["consulta"],
        int(args.get("limite", 5)),
        decrypt_secret(connector.get("apiKeyCifrada")),
        connector.get("baseUrl") or "https://api.search.brave.com/res/v1/web/search",
    )


register(Tool("web.abrir_url", "Abre una URL pública permitida por el playbook.", {"type": "object", "required": ["url"], "properties": {"url": {"type": "string", "format": "uri"}}}, "ia.agent.ask", False, "ask", "medio", _web_open))
register(Tool("web.resolver_doi", "Resuelve metadatos científicos mediante Crossref.", {"type": "object", "required": ["doi"], "properties": {"doi": {"type": "string"}}}, "ia.agent.ask", False, "ask", "bajo", _doi))
register(Tool("web.buscar", "Busca fuentes en la web mediante Brave.", {"type": "object", "required": ["consulta"], "properties": {"consulta": {"type": "string"}, "limite": {"type": "integer", "minimum": 1, "maximum": 10}}}, "ia.agent.ask", False, "ask", "medio", _search))


async def _shell(args: dict, ctx: dict) -> Any:
    if not ctx.get("isSuperadmin") or not ctx.get("shellEnabled"):
        raise PermissionError("El modo shell no está habilitado para esta conversación.")
    blocked = (ctx.get("policy") or {}).get("comandosBloqueados") or []
    return await execute_shell(args["comando"], args.get("cwd"), blocked)


register(Tool("sistema.ejecutar_comando", "Ejecuta argv local dentro del workdir permitido.", {"type": "object", "required": ["comando"], "properties": {"comando": {"type": "string"}, "cwd": {"type": "string"}}}, "ia.shell.execute", True, "super", "critico", _shell))
