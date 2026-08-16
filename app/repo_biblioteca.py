"""Biblioteca de bibliografía (artículos de fagoterapia en PDF)."""
from __future__ import annotations

import os
from pathlib import Path

from .db import get_conn

# Carpeta donde se guardan los PDFs de la biblioteca (fuera de git).
BIBLIO_DIR = Path(__file__).resolve().parent.parent / "_biblioteca"

CATEGORIAS_VALIDAS = {"referencia_doctora", "referencia_mi_articulo", "publicado"}


def listar(categoria: str | None = None) -> list[dict]:
    where = ""
    params: tuple = ()
    if categoria and categoria in CATEGORIAS_VALIDAS:
        where = "where categoria = %s"
        params = (categoria,)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select id_documento::text as id, titulo, coalesce(autores,'') as autores,
                   anio, coalesce(fuente,'') as fuente, categoria,
                   coalesce(notas,'') as notas, coalesce(doi,'') as doi,
                   archivo_uri as "archivoUri", archivo_nombre as "archivoNombre",
                   size_bytes as "sizeBytes",
                   to_char(created_at,'YYYY-MM-DD') as "creadoEn"
            from biblioteca {where}
            order by anio desc nulls last, created_at desc
            """,
            params,
        )
        return cur.fetchall()


def crear(p: dict, archivo_uri: str | None, archivo_nombre: str | None, size_bytes: int | None) -> dict:
    titulo = (p.get("titulo") or "").strip()
    if not titulo:
        raise ValueError("El título es obligatorio")
    categoria = p.get("categoria") or "referencia_doctora"
    if categoria not in CATEGORIAS_VALIDAS:
        categoria = "referencia_doctora"
    anio = p.get("anio")
    try:
        anio = int(anio) if anio not in (None, "") else None
    except (TypeError, ValueError):
        anio = None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into biblioteca
                  (titulo, autores, anio, fuente, categoria, notas, doi,
                   archivo_uri, archivo_nombre, size_bytes)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_documento::text as id
                """,
                (titulo, p.get("autores") or None, anio, p.get("fuente") or None,
                 categoria, p.get("notas") or None, p.get("doi") or None,
                 archivo_uri, archivo_nombre, size_bytes),
            )
            rid = cur.fetchone()["id"]
        conn.commit()
    return {"id": rid}


def actualizar(id_documento: str, p: dict) -> dict:
    campos = {
        "titulo": "titulo", "autores": "autores", "anio": "anio",
        "fuente": "fuente", "categoria": "categoria", "notas": "notas", "doi": "doi",
    }
    sets, vals = [], []
    for clave, col in campos.items():
        if clave in p:
            valor = p[clave]
            if clave == "anio":
                try:
                    valor = int(valor) if valor not in (None, "") else None
                except (TypeError, ValueError):
                    valor = None
            if clave == "categoria" and valor not in CATEGORIAS_VALIDAS:
                continue
            sets.append(f"{col}=%s")
            vals.append(valor if valor != "" else None)
    if not sets:
        return {"id": id_documento}
    vals.append(id_documento)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update biblioteca set {', '.join(sets)} where id_documento=%s "
                "returning id_documento::text as id",
                vals,
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Documento no encontrado")
        conn.commit()
    return {"id": id_documento}


def eliminar(id_documento: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select archivo_uri from biblioteca where id_documento=%s", (id_documento,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Documento no encontrado")
            uri = row["archivo_uri"]
            cur.execute("delete from biblioteca where id_documento=%s", (id_documento,))
        conn.commit()
    # Borra el PDF del disco.
    if uri:
        try:
            nombre = os.path.basename(uri)
            ruta = BIBLIO_DIR / nombre
            if nombre and ruta.is_file():
                ruta.unlink()
        except OSError:
            pass
    return {"ok": True}
