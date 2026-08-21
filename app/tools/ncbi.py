"""Herramientas contra los servicios públicos de NCBI.

Tres cosas distintas viven aquí:

    descargar_ncbi     efetch  — baja una secuencia de referencia por accession
    blast_enviar       BLAST   — manda la consulta y devuelve el identificador del trabajo
    blast_resultado    BLAST   — consulta ese trabajo y, si terminó, devuelve los hits
    resolver_taxonomia efetch  — linaje completo a partir de un taxid

BLAST se parte en enviar/consultar a propósito: el servicio es asíncrono de verdad
(NCBI devuelve un RID y hay que preguntar por él), así que partirlo hace que cada
herramienta sea rápida y honesta sobre lo que hace. Quien orqueste —nuestro bucle, n8n
o un agente externo— hace el ciclo de espera con la cadencia que quiera.

Etiqueta con NCBI: identificarse con `tool`, opcionalmente `email`, y no consultar el
estado más de una vez cada pocos segundos. `NCBI_EMAIL` se configura por entorno.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import httpx

from .base import ToolError, registrar

BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI pide que las aplicaciones se identifiquen.
TOOL_NAME = "fagolab"
EMAIL = os.environ.get("NCBI_EMAIL", "")

# Bases de datos útiles para este laboratorio. La 16S curada es la correcta para el
# flujo real de Pamela (su PCR es de 16S) y además es más rápida que la general.
#
# CUIDADO: el nombre de la base en la URL API no es el que muestra la interfaz web. Con
# "16S_ribosomal_RNA" el servicio responde `ThereAreHits=no` sin ningún error — una
# secuencia 16S perfecta parece no tener coincidencias. El nombre válido lleva prefijo.
BASES = {
    "rRNA_typestrains/16S_ribosomal_RNA":
        "Secuencias 16S de cepas tipo, curadas. Rápida y taxonómicamente fiable.",
    "core_nt": "Nucleótidos general. Más amplia y bastante más lenta.",
}

# Nombres cortos que sí puede escribir una persona (o un modelo) sin equivocarse.
ALIAS_BASES = {
    "16S": "rRNA_typestrains/16S_ribosomal_RNA",
    "16s": "rRNA_typestrains/16S_ribosomal_RNA",
    "16S_ribosomal_RNA": "rRNA_typestrains/16S_ribosomal_RNA",
    "nt": "core_nt",
}

BASE_POR_DEFECTO = "rRNA_typestrains/16S_ribosomal_RNA"

TIMEOUT = httpx.Timeout(60.0, connect=20.0)


def _params(extra: dict) -> dict:
    base = {"tool": TOOL_NAME}
    if EMAIL:
        base["email"] = EMAIL
    base.update(extra)
    return base


def _get(url: str, params: dict) -> httpx.Response:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            respuesta = client.get(url, params=_params(params))
    except httpx.HTTPError as e:
        raise ToolError(f"No se pudo contactar a NCBI: {e}")
    if respuesta.status_code >= 400:
        raise ToolError(f"NCBI respondió {respuesta.status_code}.")
    return respuesta


# --------------------------------------------------------------------------------------
# efetch
# --------------------------------------------------------------------------------------

@registrar(
    "descargar_ncbi",
    "Descarga de NCBI la secuencia de referencia correspondiente a un accession "
    "(por ejemplo NR_119039.1) en formato FASTA.",
    parametros={
        "type": "object",
        "properties": {
            "accession": {"type": "string", "description": "Accession de NCBI Nucleotide."},
        },
        "required": ["accession"],
    },
    devuelve="Objeto con accession, encabezado, secuencia, longitud y el FASTA completo.",
    permiso="secuenciacion.records.view",
    red=True,
)
def descargar_ncbi(entrada: dict) -> dict:
    accession = entrada["accession"].strip()
    texto = _get(
        f"{EUTILS}/efetch.fcgi",
        {"db": "nuccore", "id": accession, "rettype": "fasta", "retmode": "text"},
    ).text.strip()
    if not texto.startswith(">"):
        raise ToolError(f"NCBI no devolvió un FASTA para {accession}.")
    lineas = texto.splitlines()
    secuencia = "".join(l.strip() for l in lineas[1:] if not l.startswith(">"))
    return {
        "accession": accession,
        "fuente": "NCBI Nucleotide",
        "encabezado": lineas[0][1:].strip(),
        "secuencia": secuencia,
        "longitudPb": len(secuencia),
        "fasta": texto,
    }


@registrar(
    "resolver_taxonomia",
    "Devuelve el linaje taxonómico completo de un organismo a partir de su taxid de NCBI. "
    "El taxid siempre proviene de un hit medido de BLAST, nunca se infiere.",
    parametros={
        "type": "object",
        "properties": {
            "taxid": {"type": "string", "description": "Identificador taxonómico de NCBI."},
        },
        "required": ["taxid"],
    },
    devuelve="Objeto con nombre científico, rango y linaje como lista ordenada.",
    permiso="secuenciacion.records.view",
    red=True,
)
def resolver_taxonomia(entrada: dict) -> dict:
    taxid = str(entrada["taxid"]).strip()
    xml = _get(f"{EUTILS}/efetch.fcgi", {"db": "taxonomy", "id": taxid, "retmode": "xml"}).text
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ToolError(f"NCBI devolvió una respuesta de taxonomía ilegible: {e}")
    taxon = raiz.find("Taxon")
    if taxon is None:
        raise ToolError(f"NCBI no reconoce el taxid {taxid}.")

    linaje = [
        {
            "rango": (item.findtext("Rank") or "").strip(),
            "nombre": (item.findtext("ScientificName") or "").strip(),
        }
        for item in taxon.findall("./LineageEx/Taxon")
    ]
    return {
        "taxid": taxid,
        "nombreCientifico": (taxon.findtext("ScientificName") or "").strip(),
        "rango": (taxon.findtext("Rank") or "").strip(),
        "linaje": [n for n in linaje if n["nombre"]],
        "linajeTexto": (taxon.findtext("Lineage") or "").strip(),
    }


# --------------------------------------------------------------------------------------
# BLAST
# --------------------------------------------------------------------------------------

@registrar(
    "blast_enviar",
    "Envía una secuencia a BLAST de NCBI y devuelve el identificador del trabajo (RID). "
    "No espera el resultado: para eso está blast_resultado.",
    parametros={
        "type": "object",
        "properties": {
            "secuencia": {"type": "string", "description": "Secuencia de nucleótidos."},
            "baseDatos": {
                "type": "string",
                "description": "Acepta '16S' (por defecto, curada y rápida) o 'nt' para la general.",
            },
            "maxHits": {"type": "integer", "description": "Número de hits a pedir (por defecto 10)."},
        },
        "required": ["secuencia"],
    },
    devuelve="Objeto con rid, baseDatos y segundos estimados de espera (rtoe).",
    permiso="secuenciacion.records.create",
    plano="job",
    red=True,
)
def blast_enviar(entrada: dict) -> dict:
    secuencia = re.sub(r"\s+", "", entrada["secuencia"]).upper()
    if len(secuencia) < 50:
        raise ToolError("La secuencia es demasiado corta para BLAST (mínimo 50 bases).")
    solicitada = (entrada.get("baseDatos") or "").strip() or BASE_POR_DEFECTO
    base = ALIAS_BASES.get(solicitada, solicitada)
    if base not in BASES:
        raise ToolError(
            f"Base de datos no soportada: {solicitada}. "
            f"Opciones: {', '.join(BASES)} (o los alias 16S / nt)."
        )

    respuesta = _get(
        BLAST_URL,
        {
            "CMD": "Put",
            "PROGRAM": "blastn",
            "MEGABLAST": "on",
            "DATABASE": base,
            "QUERY": secuencia,
            "HITLIST_SIZE": int(entrada.get("maxHits") or 10),
            "FORMAT_TYPE": "JSON2_S",
        },
    )
    rid = re.search(r"RID\s*=\s*(\S+)", respuesta.text)
    if not rid:
        raise ToolError("NCBI no devolvió un identificador de trabajo (RID) para esta consulta.")
    rtoe = re.search(r"RTOE\s*=\s*(\d+)", respuesta.text)
    return {
        "rid": rid.group(1),
        "baseDatos": base,
        "esperaEstimadaS": int(rtoe.group(1)) if rtoe else 30,
        "longitudConsultaPb": len(secuencia),
    }


def _cobertura(hsps: list[dict], largo_consulta: int) -> float:
    """Cobertura de la consulta uniendo los intervalos alineados, sin contar solapes."""
    if not largo_consulta:
        return 0.0
    intervalos = sorted(
        (min(h.get("query_from", 0), h.get("query_to", 0)), max(h.get("query_from", 0), h.get("query_to", 0)))
        for h in hsps
    )
    cubierto = 0
    fin_previo = 0
    for inicio, fin in intervalos:
        if fin <= fin_previo:
            continue
        cubierto += fin - max(inicio, fin_previo + 1) + 1
        fin_previo = fin
    return round(100 * cubierto / largo_consulta, 2)


def _parsear_hits(datos: dict) -> dict:
    salida = datos.get("BlastOutput2")
    if isinstance(salida, list):
        salida = salida[0] if salida else {}
    reporte = (salida or {}).get("report") or {}
    resultados = (reporte.get("results") or {}).get("search") or {}
    largo_consulta = int(resultados.get("query_len") or 0)

    hits: list[dict] = []
    for posicion, hit in enumerate(resultados.get("hits") or [], start=1):
        descripcion = (hit.get("description") or [{}])[0]
        hsps = hit.get("hsps") or []
        mejor = hsps[0] if hsps else {}
        largo_alineamiento = int(mejor.get("align_len") or 0)
        identidades = int(mejor.get("identity") or 0)
        hits.append({
            "ranking": posicion,
            "accession": descripcion.get("accession") or descripcion.get("id") or "",
            "organismo": descripcion.get("sciname") or descripcion.get("title") or "",
            "titulo": descripcion.get("title") or "",
            "taxid": str(descripcion.get("taxid") or ""),
            "identidadPct": round(100 * identidades / largo_alineamiento, 2) if largo_alineamiento else None,
            "coberturaPct": _cobertura(hsps, largo_consulta),
            "eValue": mejor.get("evalue"),
            "bitScore": mejor.get("bit_score"),
            "longitudAlineamientoPb": largo_alineamiento or None,
        })
    return {
        "consultaLongitudPb": largo_consulta,
        "programa": reporte.get("program", "blastn"),
        "version": reporte.get("version", ""),
        "hits": hits,
    }


@registrar(
    "blast_resultado",
    "Consulta un trabajo de BLAST por su RID. Si todavía corre devuelve estado "
    "'en_curso'; si terminó devuelve los hits con identidad, cobertura y e-value.",
    parametros={
        "type": "object",
        "properties": {
            "rid": {"type": "string", "description": "Identificador del trabajo devuelto por blast_enviar."},
        },
        "required": ["rid"],
    },
    devuelve="Objeto con estado (en_curso | completado | sin_hits | fallido) y la lista de hits.",
    permiso="secuenciacion.records.view",
    red=True,
)
def blast_resultado(entrada: dict) -> dict:
    rid = entrada["rid"].strip()
    info = _get(BLAST_URL, {"CMD": "Get", "FORMAT_OBJECT": "SearchInfo", "RID": rid}).text

    if "Status=WAITING" in info:
        return {"rid": rid, "estado": "en_curso", "hits": []}
    if "Status=FAILED" in info:
        raise ToolError(f"BLAST reportó que el trabajo {rid} falló.")
    if "Status=UNKNOWN" in info:
        raise ToolError(f"BLAST ya no reconoce el trabajo {rid}: expiró o el RID es inválido.")
    if "ThereAreHits=no" in info:
        return {"rid": rid, "estado": "sin_hits", "hits": []}

    respuesta = _get(BLAST_URL, {"CMD": "Get", "FORMAT_TYPE": "JSON2_S", "RID": rid})
    try:
        datos = respuesta.json()
    except ValueError:
        raise ToolError("BLAST devolvió un resultado que no se pudo interpretar como JSON.")
    return {"rid": rid, "estado": "completado", **_parsear_hits(datos)}
