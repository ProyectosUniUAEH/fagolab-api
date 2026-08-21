"""Rutas de secuenciación y archivos de secuencia (`/api/secuenciaciones`).

Continúa el flujo después del gel. La validación y el QC se resuelven aquí mismo, al
subir el archivo: son deterministas y no dependen de ningún servicio externo.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import re
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from . import repo_seq, seq_qc
from .config import settings

router = APIRouter(prefix="/api/secuenciaciones", tags=["secuenciacion"])

# Un FASTQ de MiSeq comprimido ronda cientos de MB; para la app se acepta hasta 200 MB.
MAX_BYTES = 200 * 1024 * 1024

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _carpeta() -> str:
    destino = os.path.join(settings.MEDIA_DIR, "secuencias")
    os.makedirs(destino, exist_ok=True)
    return destino


def _quien(request: Request) -> str:
    user = getattr(request.state, "user", None) or {}
    return user.get("nombre") or user.get("correo") or ""


# ---- Lecturas -------------------------------------------------------------------------
@router.get("")
def listar():
    return repo_seq.list_secuenciaciones()


@router.get("/candidatos")
def candidatos():
    """Carriles de gel con banda positiva que aún no se enviaron a secuenciar."""
    return repo_seq.list_candidatos()


@router.get("/{id_secuenciacion}")
def detalle(id_secuenciacion: str):
    try:
        return repo_seq.get_secuenciacion(id_secuenciacion)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---- Escrituras -----------------------------------------------------------------------
@router.post("")
def crear(payload: dict):
    try:
        return repo_seq.crear_secuenciacion(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/{id_secuenciacion}")
def actualizar(id_secuenciacion: str, payload: dict):
    try:
        return repo_seq.actualizar_secuenciacion(id_secuenciacion, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{id_secuenciacion}")
def eliminar(id_secuenciacion: str):
    try:
        return repo_seq.eliminar_secuenciacion(id_secuenciacion)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---- Archivos FASTQ / FASTA -----------------------------------------------------------
@router.post("/{id_secuenciacion}/archivos")
async def subir_archivo(
    id_secuenciacion: str,
    request: Request,
    file: UploadFile = File(...),
    rol: str = Form("consenso"),
):
    """Sube un FASTQ/FASTA, lo valida y calcula su QC en el momento.

    Un archivo inválido también se guarda: el motivo del rechazo es información útil
    para el laboratorio y queda en el historial.
    """
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"El archivo supera el límite de {MAX_BYTES // (1024 * 1024)} MB.")

    nombre_original = file.filename or "secuencia"
    analisis = seq_qc.validar(raw, nombre_original)

    destino = _carpeta()
    safe = _SAFE.sub("_", nombre_original)[:60]
    fname = f"{uuid.uuid4().hex[:12]}_{safe}"
    with open(os.path.join(destino, fname), "wb") as f:
        f.write(raw)

    try:
        resultado = repo_seq.guardar_archivo(
            id_secuenciacion,
            {
                "formato": analisis["formato"] or "fasta",
                "rol": rol,
                "nombreArchivo": nombre_original,
                "storageUri": f"/media/secuencias/{fname}",
                "comprimido": analisis["comprimido"],
                "sizeBytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "valido": analisis["valido"],
                "semaforo": analisis["semaforo"],
                "metricas": analisis["metricas"],
                "hallazgos": analisis["hallazgos"],
                "subidoPor": _quien(request),
            },
        )
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {**resultado, **analisis, "nombreArchivo": nombre_original}


@router.delete("/archivos/{id_archivo}")
def borrar_archivo(id_archivo: str):
    try:
        resultado = repo_seq.eliminar_archivo(id_archivo)
    except ValueError as e:
        raise HTTPException(404, str(e))
    ruta = os.path.join(settings.MEDIA_DIR, resultado["storageUri"].replace("/media/", "", 1))
    if os.path.isfile(ruta):
        os.remove(ruta)
    return {"ok": True}


@router.get("/archivos/{id_archivo}/vista-previa", response_class=PlainTextResponse)
def vista_previa(id_archivo: str, lineas: int = 20):
    """Primeras líneas del archivo: sirve para mostrar en la UI que un FASTQ trae
    calidad y un FASTA no. Es la diferencia que hay que saber explicar."""
    try:
        meta = repo_seq.contenido_archivo(id_archivo)
    except ValueError as e:
        raise HTTPException(404, str(e))
    ruta = os.path.join(settings.MEDIA_DIR, meta["storageUri"].replace("/media/", "", 1))
    if not os.path.isfile(ruta):
        raise HTTPException(404, "El archivo ya no está en el almacenamiento.")
    tope = max(1, min(lineas, 200))
    # Se lee línea a línea (y por gzip si toca) para no cargar en memoria un FASTQ entero.
    abrir = gzip.open if meta["comprimido"] else open
    recortadas: list[str] = []
    with abrir(ruta, "rt", encoding="utf-8", errors="replace") as f:
        for linea in f:
            recortadas.append(linea.rstrip("\n"))
            if len(recortadas) >= tope:
                break
    return "\n".join(recortadas)
