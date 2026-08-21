"""Rutas del análisis automatizado y del catálogo de herramientas.

    GET  /api/tools                    contrato público del catálogo
    POST /api/tools/{nombre}           ejecuta una herramienta (misma puerta que la CLI)
    POST /api/secuenciaciones/{id}/analisis   lanza BLAST en segundo plano
    GET  /api/analisis/{id}            estado de una corrida
    GET  /api/secuenciaciones/{id}/hits       coincidencias guardadas
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import orquestador, repo_analisis, repo_fichas, repo_seq, tools
from .tools.base import ToolError

router = APIRouter(prefix="/api", tags=["analisis"])


def _quien(request: Request) -> str:
    user = getattr(request.state, "user", None) or {}
    return user.get("nombre") or user.get("correo") or ""


@router.get("/tools")
def catalogo_tools():
    """El contrato que consumen el agente, la CLI y cualquier orquestador externo."""
    return {"herramientas": tools.catalogo()}


@router.post("/tools/{nombre}")
def ejecutar_tool(nombre: str, request: Request, payload: dict | None = None):
    """Ejecuta una herramienta del catálogo.

    Doble candado, igual que en el agente: el ACL de la ruta autoriza *usar el catálogo*,
    y aquí se comprueba además el permiso propio de esa herramienta. Sin eso, un permiso
    genérico abriría todas las herramientas de golpe.

    Las de plano 'job' se pueden invocar aquí, pero tardan: para BLAST completo usa
    /api/secuenciaciones/{id}/analisis, que devuelve de inmediato y corre en segundo plano.
    """
    try:
        herramienta = tools.obtener(nombre)
    except ToolError as e:
        raise HTTPException(404, str(e))

    user = getattr(request.state, "user", None) or {}
    if (
        herramienta.permiso
        and not user.get("isSuperadmin")
        and herramienta.permiso not in set(user.get("permissions") or [])
    ):
        raise HTTPException(403, f"Te falta el permiso '{herramienta.permiso}' para esta herramienta.")

    try:
        return tools.ejecutar(nombre, payload or {})
    except ToolError as e:
        raise HTTPException(400, str(e))


@router.post("/secuenciaciones/{id_secuenciacion}/analisis")
def lanzar_analisis(id_secuenciacion: str, request: Request, payload: dict | None = None):
    """Arranca la identificación taxonómica sobre el FASTA de la secuenciación."""
    payload = payload or {}
    try:
        secuenciacion = repo_seq.get_secuenciacion(id_secuenciacion)
    except ValueError as e:
        raise HTTPException(404, str(e))

    # Sin archivo indicado se toma el FASTA válido más reciente: es lo que la científica
    # espera cuando aprieta "Analizar" sin elegir nada.
    id_archivo = payload.get("idArchivo") or _fasta_valido(secuenciacion)

    corrida = repo_analisis.lanzar_blast(
        id_secuenciacion,
        id_archivo,
        {
            "baseDatos": payload.get("baseDatos") or "",
            "maxHits": int(payload.get("maxHits") or 10),
        },
        _quien(request),
    )
    return corrida


def _fasta_valido(secuenciacion: dict) -> str:
    """El FASTA válido más reciente. Es lo que se analiza cuando nadie elige archivo."""
    candidatos = [
        a for a in secuenciacion["archivos"]
        if a["formato"] == "fasta" and a["estadoValidacion"] == "valido"
    ]
    if not candidatos:
        raise HTTPException(
            409,
            "Esta secuenciación no tiene un FASTA válido. Se necesita una secuencia "
            "procesada; un FASTQ son lecturas crudas.",
        )
    return candidatos[-1]["id"]


@router.post("/secuenciaciones/{id_secuenciacion}/arbol")
def lanzar_arbol(id_secuenciacion: str, request: Request):
    """Construye el árbol orientativo contra las referencias de los hits de BLAST."""
    try:
        secuenciacion = repo_seq.get_secuenciacion(id_secuenciacion)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return repo_analisis.lanzar_arbol(id_secuenciacion, _fasta_valido(secuenciacion), _quien(request))


# ---- Orquestación completa: un solo disparo -------------------------------------------
@router.get("/orquestacion/definicion")
def definicion_flujo():
    """El flujo tal como se dibuja: nodos, naturaleza, conexiones y por qué de cada paso."""
    return orquestador.definicion()


@router.post("/secuenciaciones/{id_secuenciacion}/orquestar")
def orquestar(id_secuenciacion: str, request: Request, payload: dict | None = None):
    """Corre el pipeline entero: validación, QC, BLAST, taxonomía, árbol, y los tres
    pasos generativos. Devuelve de inmediato; el avance se consulta en /api/analisis/{id}."""
    payload = payload or {}
    try:
        secuenciacion = repo_seq.get_secuenciacion(id_secuenciacion)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return orquestador.lanzar(
        id_secuenciacion, _fasta_valido(secuenciacion), payload, _quien(request)
    )


# ---- Experimentos: variar una variable y comparar --------------------------------------
@router.get("/experimentos/variables")
def variables_experimento():
    return {"variables": repo_fichas.variables_experimento()}


@router.post("/secuenciaciones/{id_secuenciacion}/experimento")
def lanzar_experimento(id_secuenciacion: str, request: Request, payload: dict | None = None):
    """Genera una ficha por cada valor de la variable, dejando el resto fijo."""
    try:
        repo_seq.get_secuenciacion(id_secuenciacion)
        return repo_fichas.lanzar_experimento(id_secuenciacion, payload or {}, _quien(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/analisis/{id_corrida}")
def estado_analisis(id_corrida: str):
    try:
        return repo_analisis.get_corrida(id_corrida)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/secuenciaciones/{id_secuenciacion}/analisis")
def corridas_de_secuenciacion(id_secuenciacion: str):
    return repo_analisis.list_corridas(id_secuenciacion)


@router.get("/secuenciaciones/{id_secuenciacion}/hits")
def hits_de_secuenciacion(id_secuenciacion: str):
    return repo_analisis.list_hits(id_secuenciacion)


# ---- Ficha científica (el único paso generativo) --------------------------------------
@router.post("/secuenciaciones/{id_secuenciacion}/ficha")
def generar_ficha(id_secuenciacion: str, request: Request, payload: dict | None = None):
    """Genera la interpretación científica de la muestra con el modelo configurado.

    Es una llamada bloqueante: la ficha solo tiene sentido completa, y el modelo tarda
    segundos, no minutos como BLAST.
    """
    try:
        return repo_fichas.generar_ficha(id_secuenciacion, payload or {}, _quien(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/secuenciaciones/{id_secuenciacion}/fichas")
def fichas_de_secuenciacion(id_secuenciacion: str):
    return repo_fichas.list_fichas(id_secuenciacion)


@router.get("/secuenciaciones/{id_secuenciacion}/evidencia")
def evidencia_de_secuenciacion(id_secuenciacion: str):
    """La evidencia tal como se le entregaría al modelo, sin generar nada.

    Sirve para revisar qué va a leer antes de gastar una llamada, y para explicar en la
    demostración qué es exactamente el 'grounding'.
    """
    try:
        return repo_fichas.reunir_evidencia(id_secuenciacion)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/fichas/{id_ficha}")
def borrar_ficha(id_ficha: str):
    try:
        return repo_fichas.eliminar_ficha(id_ficha)
    except ValueError as e:
        raise HTTPException(404, str(e))
