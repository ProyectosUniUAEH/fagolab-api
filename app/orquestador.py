"""Orquestación completa del análisis: un solo disparo, nueve pasos, traza en vivo.

La interfaz llama esto una vez y a partir de ahí solo consulta el estado. Cada paso
publica en qué va, cuánto tardó y **de qué naturaleza es**, que es lo que permite dibujar
el flujo y explicarlo:

    det   código determinista        validación, QC, árbol, persistencia
    ext   servicio externo           BLAST, taxonomía, PubMed
    gen   modelo generativo          consultas, priorización, ficha

El orden no es casual: los tres pasos generativos están intercalados entre los
deterministas, no al final. El modelo decide qué buscar (A), luego el sistema busca (6),
luego el modelo decide qué sirve (B) y finalmente redacta (C).

Nada de lo que produce el modelo es un dato medido. Nada de lo que mide el sistema lo
toca el modelo.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from . import repo_analisis, repo_fichas, repo_seq
from .db import get_conn
from .tools import ejecutar
from .tools.base import ToolError

# Definición del flujo. `clave` es lo que usa el frontend para ubicar cada nodo.
PASOS = [
    {"clave": "validacion",   "etiqueta": "1", "titulo": "Validación de secuencia",   "naturaleza": "det"},
    {"clave": "qc",           "etiqueta": "2", "titulo": "Control de calidad",        "naturaleza": "det"},
    {"clave": "blast",        "etiqueta": "3", "titulo": "BLAST en NCBI",             "naturaleza": "ext"},
    {"clave": "taxonomia",    "etiqueta": "4", "titulo": "Taxonomía",                 "naturaleza": "ext"},
    {"clave": "arbol",        "etiqueta": "5", "titulo": "Árbol filogenético",        "naturaleza": "det"},
    {"clave": "consultas",    "etiqueta": "A", "titulo": "Redacción de consultas",    "naturaleza": "gen"},
    {"clave": "pubmed",       "etiqueta": "6", "titulo": "Búsqueda en PubMed",        "naturaleza": "ext"},
    {"clave": "priorizacion", "etiqueta": "B", "titulo": "Priorización de evidencia", "naturaleza": "gen"},
    {"clave": "ficha",        "etiqueta": "C", "titulo": "Generación de ficha científica", "naturaleza": "gen"},
]

# Cuánto tarda típicamente cada paso. Solo sirve para estimar lo que falta en pantalla.
DURACION_TIPICA = {
    "validacion": 0.1, "qc": 0.1, "blast": 75, "taxonomia": 1.5, "arbol": 35,
    "consultas": 6, "pubmed": 14, "priorizacion": 12, "ficha": 17,
}

# Por qué cada paso es de su naturaleza. Se muestra en la interfaz: es la explicación
# que hay que poder dar, y vivir junto al código evita que se desincronice.
PORQUE = {
    "validacion": "Comprobar un formato es una operación exacta. Un modelo devolvería un veredicto plausible.",
    "qc": "Contar bases y calcular calidad son cuentas. Pedírselo a un modelo sería inventar números.",
    "blast": "Alineamiento de secuencias contra la base mundial de referencia. Algoritmo, no criterio.",
    "taxonomia": "El linaje se consulta con el identificador del hit medido, no se recuerda.",
    "arbol": "Distancias y agrupamiento calculados localmente, sin intervención del modelo.",
    "consultas": "Convertir el organismo y el contexto en las preguntas correctas es criterio, no plantilla.",
    "pubmed": "Las consultas las escribió el modelo; ejecutarlas contra PubMed es determinista.",
    "priorizacion": "De decenas de artículos, decidir cuáles sostienen una decisión experimental.",
    "ficha": "Interpretar y redactar sobre hechos ya medidos. El único texto nuevo del pipeline.",
}


def plantilla_pasos() -> list[dict]:
    return [
        {**paso, "estado": "pendiente", "duracionMs": None, "detalle": "", "datos": {}}
        for paso in PASOS
    ]


class Corrida:
    """Estado vivo de una orquestación. Escribe en la BD para que la UI lo consulte."""

    def __init__(self, id_corrida: str) -> None:
        self.id = id_corrida
        self.pasos = plantilla_pasos()
        self.bitacora: list[dict] = []
        self.inicio = time.monotonic()

    # -- persistencia ------------------------------------------------------------------
    def _guardar(self, **extra) -> None:
        sets = ["pasos = %s", "bitacora = %s"]
        valores: list = [json.dumps(self.pasos, ensure_ascii=False, default=str),
                         json.dumps(self.bitacora[-80:], ensure_ascii=False, default=str)]
        for columna, valor in extra.items():
            sets.append(f"{columna} = %s")
            valores.append(valor)
        valores.append(self.id)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"update corridas_analisis set {', '.join(sets)} where id_corrida = %s",
                    tuple(valores),
                )
            conn.commit()

    def _paso(self, clave: str) -> dict:
        return next(p for p in self.pasos if p["clave"] == clave)

    # -- eventos -----------------------------------------------------------------------
    def iniciar(self, clave: str, detalle: str = "") -> float:
        paso = self._paso(clave)
        paso["estado"] = "en_curso"
        paso["detalle"] = detalle
        self._guardar(progreso=paso["titulo"], estado_corrida="en_curso")
        return time.monotonic()

    def terminar(self, clave: str, desde: float, detalle: str, datos: dict | None = None) -> None:
        paso = self._paso(clave)
        paso["estado"] = "completado"
        paso["duracionMs"] = int((time.monotonic() - desde) * 1000)
        paso["detalle"] = detalle
        paso["datos"] = datos or {}
        self.log(paso["naturaleza"], paso["titulo"], detalle, paso["duracionMs"])
        self._guardar()

    def fallar(self, clave: str, motivo: str) -> None:
        paso = self._paso(clave)
        paso["estado"] = "fallido"
        paso["detalle"] = motivo
        self.log(paso["naturaleza"], paso["titulo"], f"falló: {motivo}", None)
        self._guardar(estado_corrida="fallida", error=motivo)

    def log(self, naturaleza: str, titulo: str, detalle: str, ms: int | None = None) -> None:
        self.bitacora.append({
            "hora": datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S"),
            "naturaleza": naturaleza,
            "titulo": titulo,
            "detalle": detalle,
            "duracionMs": ms,
        })

    def nota(self, naturaleza: str, titulo: str, detalle: str) -> None:
        """Traza intermedia dentro de un paso (por ejemplo, cada consulta a PubMed)."""
        self.log(naturaleza, titulo, detalle)
        self._guardar()


# --------------------------------------------------------------------------------------
# El flujo
# --------------------------------------------------------------------------------------

def _ejecutar(id_corrida: str, id_sec: str, id_archivo: str, opciones: dict, quien: str) -> None:
    corrida = Corrida(id_corrida)
    contexto = opciones.get("contexto") or "fagoterapia en acuicultura de tilapia"
    max_articulos = int(opciones.get("maxArticulos") or 8)

    try:
        # 1 — Validación
        t = corrida.iniciar("validacion", "leyendo el archivo")
        qc = ejecutar("qc_secuencia", {"idArchivo": id_archivo})
        if not qc["valido"]:
            corrida.fallar("validacion", qc["hallazgos"][0] if qc["hallazgos"] else "archivo inválido")
            return
        corrida.terminar("validacion", t, f"{qc['formato'].upper()} válido",
                         {"formato": qc["formato"], "archivo": qc["nombreArchivo"]})

        # 2 — Control de calidad
        t = corrida.iniciar("qc", "calculando métricas")
        m = qc["metricas"]
        corrida.terminar(
            "qc", t,
            f"{m['longitudTotalPb']:,} pb · GC {m['gcPct']}% · semáforo {qc['semaforo']}",
            {"longitudPb": m["longitudTotalPb"], "gcPct": m["gcPct"],
             "ambiguasPct": m["basesAmbiguasPct"], "semaforo": qc["semaforo"]},
        )

        lectura = ejecutar("leer_secuencia", {"idArchivo": id_archivo})

        # 3 — BLAST
        t = corrida.iniciar("blast", "enviando la consulta a NCBI")
        envio = ejecutar("blast_enviar", {"secuencia": lectura["secuencia"], "baseDatos": "16S"})
        corrida.nota("ext", "BLAST en NCBI", f"trabajo {envio['rid']} aceptado · esperando resultado")
        while True:
            time.sleep(10)
            estado = ejecutar("blast_resultado", {"rid": envio["rid"]})
            if estado["estado"] != "en_curso":
                break
        hits = estado.get("hits") or []
        if not hits:
            corrida.fallar("blast", "BLAST no encontró coincidencias para esta secuencia.")
            return
        repo_analisis.guardar_hits_blast(id_sec, envio["rid"], envio["baseDatos"], hits)
        principal = hits[0]
        corrida.terminar(
            "blast", t,
            f"{len(hits)} coincidencias · principal {principal['organismo']} "
            f"({principal['identidadPct']}%)",
            {"total": len(hits), "rid": envio["rid"], "organismo": principal["organismo"],
             "identidadPct": principal["identidadPct"],
             "coberturaPct": principal["coberturaPct"],
             "accession": principal["accession"],
             "hits": [{k: h[k] for k in ("ranking", "organismo", "identidadPct",
                                         "coberturaPct", "accession")} for h in hits[:6]]},
        )

        # 4 — Taxonomía
        t = corrida.iniciar("taxonomia", "resolviendo el linaje del hit principal")
        taxid = principal.get("taxid") or principal.get("taxId")
        taxonomia = None
        if taxid:
            try:
                taxonomia = ejecutar("resolver_taxonomia", {"taxid": taxid})
            except ToolError as e:
                corrida.nota("ext", "Taxonomía", f"no se pudo resolver: {e}")
        corrida.terminar(
            "taxonomia", t,
            taxonomia["nombreCientifico"] if taxonomia else "sin linaje disponible",
            {"linaje": [n["nombre"] for n in (taxonomia or {}).get("linaje", [])][-4:],
             "nombreCientifico": (taxonomia or {}).get("nombreCientifico", "")},
        )

        # 5 — Árbol filogenético
        t = corrida.iniciar("arbol", "descargando secuencias de referencia")
        arbol = None
        try:
            arbol = ejecutar("construir_arbol", {
                "consulta": lectura["secuencia"],
                "etiquetaConsulta": lectura["codigoSecuenciacion"],
                "accessions": [h["accession"] for h in hits if h.get("accession")][:8],
            })
            corrida.terminar("arbol", t,
                             f"{len(arbol['referencias'])} referencias · orientativo",
                             {"referencias": len(arbol["referencias"]), "newick": arbol["newick"]})
        except ToolError as e:
            corrida.terminar("arbol", t, f"omitido: {e}", {})

        # A — El modelo redacta las consultas
        t = corrida.iniciar("consultas", "el modelo está decidiendo qué buscar")
        consultas = repo_fichas.generar_consultas(principal["organismo"], contexto)
        corrida.terminar("consultas", t, f"{len(consultas)} consultas redactadas",
                         {"consultas": consultas, "contexto": contexto})

        # 6 — PubMed ejecuta esas consultas
        t = corrida.iniciar("pubmed", "consultando PubMed")
        candidatos: list[dict] = []
        vistos: set[str] = set()
        total_reportado = 0
        for consulta in consultas:
            try:
                resultado = ejecutar("buscar_pubmed", {"consulta": consulta, "maxResultados": 20})
            except ToolError as e:
                corrida.nota("ext", "PubMed", f"consulta fallida: {e}")
                continue
            nuevos = [a for a in resultado["articulos"] if a["pmid"] not in vistos]
            vistos.update(a["pmid"] for a in nuevos)
            candidatos.extend(nuevos)
            total_reportado += resultado["totalEncontrados"]
            corrida.nota("ext", "PubMed",
                         f'"{consulta}" → {resultado["totalEncontrados"]} en PubMed, '
                         f"{len(nuevos)} nuevos con resumen")
        corrida.terminar(
            "pubmed", t,
            f"{len(candidatos)} artículos únicos con resumen",
            {"candidatos": len(candidatos), "totalPubMed": total_reportado,
             "consultas": len(consultas)},
        )

        # B — El modelo decide cuáles sirven
        t = corrida.iniciar("priorizacion", f"el modelo está leyendo {len(candidatos)} resúmenes")
        seleccion = repo_fichas.priorizar_evidencia(principal["organismo"], candidatos, max_articulos)
        for articulo in seleccion:
            repo_analisis.guardar_evidencia(
                tipo="pubmed", fuente="pubmed", id_secuenciacion=id_sec,
                pmid=articulo["pmid"], titulo=articulo["titulo"], url=articulo["url"],
                contenido=articulo,
            )
        corrida.terminar(
            "priorizacion", t,
            f"{len(seleccion)} de {len(candidatos)} artículos seleccionados",
            {"seleccionados": [
                {"pmid": a["pmid"], "anio": a.get("anio", ""), "titulo": a["titulo"][:110],
                 "motivo": a.get("motivoSeleccion", ""), "url": a["url"]}
                for a in seleccion],
             "descartados": len(candidatos) - len(seleccion)},
        )

        # C — La ficha
        t = corrida.iniciar("ficha", "el modelo está redactando la interpretación")
        ficha = repo_fichas.generar_ficha(id_sec, {
            "temperatura": float(opciones.get("temperatura", 0.3)),
            "seed": opciones.get("seed", 42),
            "etiquetaExperimento": opciones.get("etiqueta") or "orquestación",
            "conEvidencia": True,
            "maxArticulos": max_articulos,
        }, quien)
        corrida.terminar(
            "ficha", t,
            f"{ficha['tokensSalida']} tokens · {ficha['modelo']}",
            {"idFicha": ficha["id"], "modelo": ficha["modelo"],
             "temperatura": ficha["temperatura"], "seed": ficha["seed"],
             "tokensEntrada": ficha["tokensEntrada"], "tokensSalida": ficha["tokensSalida"],
             "evidenciaSha256": ficha["evidenciaSha256"],
             "resumen": ficha["secciones"].get("Resumen", "")[:600]},
        )

        total = time.monotonic() - corrida.inicio
        generativo = sum(
            (p["duracionMs"] or 0) for p in corrida.pasos if p["naturaleza"] == "gen"
        ) / 1000
        corrida._guardar(
            estado_corrida="completada",
            progreso="Completada",
            resultado=json.dumps({
                "idFicha": ficha["id"],
                "organismo": principal["organismo"],
                "identidadPct": principal["identidadPct"],
                "totalHits": len(hits),
                "articulosRevisados": len(candidatos),
                "articulosSeleccionados": len(seleccion),
                "segundosTotal": round(total, 1),
                "segundosGenerativo": round(generativo, 1),
                "newick": (arbol or {}).get("newick"),
            }, ensure_ascii=False, default=str),
        )
    except ToolError as e:
        corrida._guardar(estado_corrida="fallida", error=str(e))
    except Exception as e:  # noqa: BLE001 — el hilo nunca debe morir en silencio
        corrida._guardar(estado_corrida="fallida", error=f"{type(e).__name__}: {e}")


def lanzar(id_sec: str, id_archivo: str, opciones: dict, quien: str) -> dict:
    """Arranca la orquestación completa en segundo plano y devuelve la corrida."""
    corrida = repo_analisis.crear_corrida(
        tipo="orquestacion", herramienta="pipeline_completo",
        id_secuenciacion=id_sec, id_archivo=id_archivo,
        parametros=opciones, ejecutada_por=quien,
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update corridas_analisis set pasos = %s, progreso = 'En cola' where id_corrida = %s",
                (json.dumps(plantilla_pasos(), ensure_ascii=False), corrida["id"]),
            )
        conn.commit()

    threading.Thread(
        target=_ejecutar,
        args=(corrida["id"], id_sec, id_archivo, opciones, quien),
        daemon=True,
        name=f"orq-{corrida['id'][:8]}",
    ).start()
    return corrida


def definicion() -> dict:
    """El flujo tal como se dibuja, con la explicación de por qué cada paso es lo que es."""
    return {
        "pasos": [{**p, "porque": PORQUE[p["clave"]],
                   "duracionTipicaS": DURACION_TIPICA[p["clave"]]} for p in PASOS],
        "conexiones": [
            {"desde": "taxonomia", "hasta": "consultas", "etiqueta": "organismo + contexto"},
            {"desde": "consultas", "hasta": "pubmed", "etiqueta": "consultas"},
            {"desde": "pubmed", "hasta": "priorizacion", "etiqueta": "abstracts"},
            {"desde": "priorizacion", "hasta": "ficha", "etiqueta": "evidencia elegida"},
        ],
    }
