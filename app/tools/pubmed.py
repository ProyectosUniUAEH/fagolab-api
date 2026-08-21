"""Búsqueda de literatura en PubMed (NCBI E-utilities).

Dos llamadas encadenadas: `esearch` devuelve los PMID que coinciden con la consulta y
`efetch` trae el registro completo de cada uno. Se conserva el abstract porque es lo
único que después leerá el modelo: sin abstract, una cita es un título suelto y el
modelo no tiene sobre qué apoyarse.

La consulta la redacta el agente; **ejecutarla es determinista**. Esa separación importa:
el modelo aporta el criterio de búsqueda, no los resultados.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import ToolError, registrar
from .ncbi import EUTILS, _get


def _texto_abstract(articulo: ET.Element) -> str:
    """Une las secciones del abstract conservando sus etiquetas (Background, Results…)."""
    partes: list[str] = []
    for bloque in articulo.findall(".//Abstract/AbstractText"):
        etiqueta = bloque.get("Label")
        contenido = "".join(bloque.itertext()).strip()
        if not contenido:
            continue
        partes.append(f"{etiqueta}: {contenido}" if etiqueta else contenido)
    return "\n".join(partes)


def _titulo(articulo: ET.Element) -> str:
    """Título completo, incluyendo el texto de etiquetas internas como <i> o <sub>.

    Ojo: un Element sin hijos es *falsy* en ElementTree, así que aquí hay que comparar
    contra None explícitamente. Con `or` el título se pierde justo en los artículos que
    no llevan marcado interno, que son la mayoría.
    """
    nodo = articulo.find(".//ArticleTitle")
    if nodo is None:
        return ""
    return "".join(nodo.itertext()).strip()


def _autores(articulo: ET.Element, tope: int = 4) -> str:
    nombres: list[str] = []
    for autor in articulo.findall(".//AuthorList/Author"):
        apellido = autor.findtext("LastName") or ""
        iniciales = autor.findtext("Initials") or ""
        if apellido:
            nombres.append(f"{apellido} {iniciales}".strip())
    if not nombres:
        return ""
    return ", ".join(nombres[:tope]) + (" et al." if len(nombres) > tope else "")


def _anio(articulo: ET.Element) -> str:
    for ruta in (".//Journal/JournalIssue/PubDate/Year", ".//PubDate/MedlineDate"):
        valor = articulo.findtext(ruta)
        if valor:
            return valor.strip()[:4]
    return ""


@registrar(
    "buscar_pubmed",
    "Busca artículos científicos en PubMed y devuelve PMID, título, autores, año, revista "
    "y abstract. Útil para contrastar un resultado de laboratorio con la literatura.",
    parametros={
        "type": "object",
        "properties": {
            "consulta": {
                "type": "string",
                "description": "Términos de búsqueda, en inglés. Ej.: "
                               "'Aeromonas hydrophila bacteriophage therapy aquaculture'.",
            },
            "maxResultados": {
                "type": "integer",
                "description": "Cuántos artículos traer (por defecto 5, máximo 50).",
            },
            "soloConAbstract": {
                "type": "boolean",
                "description": "Descartar los artículos sin abstract (por defecto sí).",
            },
        },
        "required": ["consulta"],
    },
    devuelve="Objeto con la consulta, el total encontrado y la lista de artículos.",
    permiso="analisis.corridas.view",
    red=True,
)
def buscar_pubmed(entrada: dict) -> dict:
    consulta = entrada["consulta"].strip()
    tope = max(1, min(int(entrada.get("maxResultados") or 5), 50))
    solo_abstract = entrada.get("soloConAbstract")
    solo_abstract = True if solo_abstract is None else bool(solo_abstract)

    # Se piden más de los necesarios porque después se descartan los que no traen abstract.
    busqueda = _get(
        f"{EUTILS}/esearch.fcgi",
        {"db": "pubmed", "term": consulta, "retmax": tope * 3, "retmode": "json", "sort": "relevance"},
    )
    try:
        datos = busqueda.json().get("esearchresult") or {}
    except ValueError:
        raise ToolError("PubMed devolvió una respuesta de búsqueda ilegible.")

    pmids = datos.get("idlist") or []
    total = int(datos.get("count") or 0)
    if not pmids:
        return {"consulta": consulta, "totalEncontrados": 0, "articulos": []}

    xml = _get(
        f"{EUTILS}/efetch.fcgi",
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
    ).text
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ToolError(f"PubMed devolvió registros ilegibles: {e}")

    articulos: list[dict] = []
    for articulo in raiz.findall(".//PubmedArticle"):
        pmid = articulo.findtext(".//PMID") or ""
        abstract = _texto_abstract(articulo)
        if solo_abstract and not abstract:
            continue
        articulos.append({
            "pmid": pmid,
            "titulo": _titulo(articulo),
            "autores": _autores(articulo),
            "anio": _anio(articulo),
            "revista": articulo.findtext(".//Journal/ISOAbbreviation")
                       or articulo.findtext(".//Journal/Title") or "",
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
        if len(articulos) >= tope:
            break

    return {
        "consulta": consulta,
        "totalEncontrados": total,
        "devueltos": len(articulos),
        "articulos": articulos,
    }
