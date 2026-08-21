"""Herramientas deterministas sobre archivos de secuencia.

Estas no salen a internet y no usan el modelo: leen un archivo del laboratorio y
devuelven hechos medidos. Envuelven la lógica que ya vive en `app/seq_qc.py` —
una sola implementación, expuesta como herramienta.
"""
from __future__ import annotations

import gzip
import os

from .. import repo_seq, seq_qc
from ..config import settings
from .base import ToolError, registrar


def _ruta(storage_uri: str) -> str:
    return os.path.join(settings.MEDIA_DIR, storage_uri.replace("/media/", "", 1))


def _leer_texto(meta: dict) -> str:
    ruta = _ruta(meta["storageUri"])
    if not os.path.isfile(ruta):
        raise ToolError(f"El archivo {meta['nombreArchivo']} ya no está en el almacenamiento.")
    abrir = gzip.open if meta["comprimido"] else open
    with abrir(ruta, "rt", encoding="utf-8", errors="replace") as f:
        return f.read()


@registrar(
    "qc_secuencia",
    "Valida un archivo FASTA/FASTQ del laboratorio y devuelve sus métricas de calidad "
    "(longitud, %GC, N50, bases ambiguas; en FASTQ además calidad Phred y %Q30).",
    parametros={
        "type": "object",
        "properties": {
            "idArchivo": {"type": "string", "description": "Identificador del archivo de secuencia."},
        },
        "required": ["idArchivo"],
    },
    devuelve="Objeto con formato, semáforo de calidad, métricas y hallazgos.",
    permiso="secuenciacion.records.view",
)
def qc_secuencia(entrada: dict) -> dict:
    try:
        meta = repo_seq.contenido_archivo(entrada["idArchivo"])
    except ValueError as e:
        raise ToolError(str(e))
    ruta = _ruta(meta["storageUri"])
    if not os.path.isfile(ruta):
        raise ToolError(f"El archivo {meta['nombreArchivo']} ya no está en el almacenamiento.")
    with open(ruta, "rb") as f:
        raw = f.read()
    resultado = seq_qc.validar(raw, meta["nombreArchivo"])
    return {
        "idArchivo": meta["id"],
        "nombreArchivo": meta["nombreArchivo"],
        "codigoSecuenciacion": meta["codigoSecuenciacion"],
        "origenDato": meta["origenDato"],
        **resultado,
    }


@registrar(
    "leer_secuencia",
    "Devuelve la primera secuencia de un archivo FASTA del laboratorio, lista para "
    "enviarse a BLAST. Recorta a un máximo de bases para no mandar un genoma completo.",
    parametros={
        "type": "object",
        "properties": {
            "idArchivo": {"type": "string", "description": "Identificador del archivo de secuencia."},
            "maxPb": {"type": "integer", "description": "Bases máximas a devolver (por defecto 20000)."},
        },
        "required": ["idArchivo"],
    },
    devuelve="Objeto con encabezado, secuencia y longitud en pares de bases.",
    permiso="secuenciacion.records.view",
)
def leer_secuencia(entrada: dict) -> dict:
    try:
        meta = repo_seq.contenido_archivo(entrada["idArchivo"])
    except ValueError as e:
        raise ToolError(str(e))
    if meta["formato"] != "fasta":
        raise ToolError(
            "Solo se puede leer una secuencia de consenso desde un FASTA. "
            "Un FASTQ son lecturas crudas: primero hay que procesarlas."
        )
    encabezado, secuencia = seq_qc.extraer_primera_secuencia(
        _leer_texto(meta), max_pb=int(entrada.get("maxPb") or 20_000)
    )
    if not secuencia:
        raise ToolError("El archivo no contiene ninguna secuencia legible.")
    return {
        "idArchivo": meta["id"],
        "codigoSecuenciacion": meta["codigoSecuenciacion"],
        "origenDato": meta["origenDato"],
        "encabezado": encabezado,
        "secuencia": secuencia,
        "longitudPb": len(secuencia),
    }
