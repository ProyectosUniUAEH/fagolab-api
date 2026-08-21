"""Corridas de análisis: trabajo que tarda minutos y no cabe en un request HTTP.

BLAST es asíncrono de verdad. Aquí vive la orquestación mínima: se crea la corrida, se
lanza un hilo que envía la consulta y va preguntando por ella, y la UI consulta el
estado. Cada resultado objetivo se persiste — los hits en `resultados_blast` y la
evidencia en `evidencias_externas`, con su hash y su fecha de consulta.

La orquestación vive aquí y no dentro de las herramientas a propósito: las herramientas
son funciones cortas y reutilizables; quién espera, cuánto y con qué cadencia es
decisión del orquestador. El día que ese orquestador sea n8n, estas herramientas no
cambian.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from .db import get_conn
from .tools import ejecutar
from .tools.base import ToolError

# Cadencia de consulta a NCBI. Su documentación pide no preguntar más seguido que esto.
INTERVALO_S = 10
ESPERA_MAX_S = 15 * 60


# --------------------------------------------------------------------------------------
# Corridas
# --------------------------------------------------------------------------------------

_SELECT = """
    select c.id_corrida::text as id, c.tipo_analisis as tipo, coalesce(c.herramienta,'') as herramienta,
           c.id_secuenciacion::text as "idSecuenciacion",
           c.id_archivo_secuencia::text as "idArchivo",
           c.estado_corrida as estado, coalesce(c.progreso,'') as progreso,
           coalesce(c.referencia_externa,'') as "referenciaExterna",
           c.parametros, c.resultado, c.error, c.pasos, c.bitacora,
           coalesce(c.ejecutada_por,'') as "ejecutadaPor",
           to_char(c.created_at,'YYYY-MM-DD HH24:MI') as "creadaEn",
           to_char(c.finalizada_en,'YYYY-MM-DD HH24:MI') as "finalizadaEn"
    from corridas_analisis c
"""


def crear_corrida(
    *, tipo: str, herramienta: str, id_secuenciacion: str, id_archivo: str | None,
    parametros: dict, ejecutada_por: str,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into corridas_analisis
                  (tipo_analisis, herramienta, id_secuenciacion, id_archivo_secuencia,
                   parametros, estado_corrida, progreso, iniciada_en, ejecutada_por)
                values (%s,%s,%s,%s,%s,'registrada','En cola',now(),%s)
                returning id_corrida::text as id
                """,
                (tipo, herramienta, id_secuenciacion, id_archivo,
                 json.dumps(parametros), ejecutada_por),
            )
            id_corrida = cur.fetchone()["id"]
        conn.commit()
    return {"id": id_corrida, "estado": "registrada"}


def _actualizar(id_corrida: str, **campos) -> None:
    columnas = {
        "estado": "estado_corrida", "progreso": "progreso", "error": "error",
        "referencia_externa": "referencia_externa",
    }
    sets, valores = [], []
    for clave, columna in columnas.items():
        if clave in campos:
            sets.append(f"{columna} = %s")
            valores.append(campos[clave])
    if "resultado" in campos:
        sets.append("resultado = %s")
        valores.append(json.dumps(campos["resultado"], default=str))
    if campos.get("estado") in ("completada", "fallida", "cancelada"):
        sets.append("finalizada_en = now()")
    if not sets:
        return
    valores.append(id_corrida)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update corridas_analisis set {', '.join(sets)} where id_corrida = %s",
                tuple(valores),
            )
        conn.commit()


def get_corrida(id_corrida: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SELECT + " where c.id_corrida = %s", (id_corrida,))
        fila = cur.fetchone()
        if not fila:
            raise ValueError("Corrida no encontrada")
        return fila


def list_corridas(id_secuenciacion: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            _SELECT + " where c.id_secuenciacion = %s order by c.created_at desc",
            (id_secuenciacion,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------------------
# Persistencia de resultados objetivos
# --------------------------------------------------------------------------------------

def guardar_hits_blast(id_secuenciacion: str, rid: str, base_datos: str, hits: list[dict]) -> int:
    """Reemplaza los hits de esa secuenciación por los de esta corrida.

    El ranking es único por secuenciación, así que volver a analizar sustituye el
    resultado anterior en vez de acumular rankings incompatibles.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from resultados_blast where id_secuenciacion = %s", (id_secuenciacion,))
            for hit in hits:
                cur.execute(
                    """
                    insert into resultados_blast
                      (id_secuenciacion, corrida_blast, base_datos, fecha_corrida, ranking,
                       accession, taxon_id, organismo, porcentaje_identidad, query_cover,
                       e_value, bit_score, longitud_alineamiento)
                    values (%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (id_secuenciacion, rid, base_datos, hit["ranking"], hit.get("accession"),
                     hit.get("taxid") or None, hit.get("organismo"), hit.get("identidadPct"),
                     hit.get("coberturaPct"), hit.get("eValue"), hit.get("bitScore"),
                     hit.get("longitudAlineamientoPb")),
                )
            cur.execute(
                "update secuenciaciones set estado_secuenciacion='analizada' "
                "where id_secuenciacion=%s and estado_secuenciacion <> 'analizada'",
                (id_secuenciacion,),
            )
        conn.commit()
    return len(hits)


def guardar_evidencia(
    *, tipo: str, fuente: str, contenido: dict, id_secuenciacion: str | None = None,
    accession: str | None = None, pmid: str | None = None, titulo: str | None = None,
    url: str | None = None,
) -> str:
    """Registra evidencia externa con su huella, para poder rastrear una ficha hasta aquí."""
    crudo = json.dumps(contenido, sort_keys=True, ensure_ascii=False, default=str)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into evidencias_externas
                  (tipo, fuente, accession, pmid, titulo, url, contenido, sha256, id_secuenciacion)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_evidencia::text as id
                """,
                (tipo, fuente, accession, pmid, titulo, url, crudo,
                 hashlib.sha256(crudo.encode("utf-8")).hexdigest(), id_secuenciacion),
            )
            id_evidencia = cur.fetchone()["id"]
        conn.commit()
    return id_evidencia


def list_hits(id_secuenciacion: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select ranking, coalesce(accession,'') as accession,
                   coalesce(organismo,'') as organismo, coalesce(taxon_id,'') as "taxId",
                   porcentaje_identidad::float as "identidadPct",
                   query_cover::float as "coberturaPct",
                   e_value::float as "eValue", bit_score::float as "bitScore",
                   longitud_alineamiento as "longitudAlineamientoPb",
                   coalesce(base_datos,'') as "baseDatos",
                   to_char(fecha_corrida,'YYYY-MM-DD HH24:MI') as "fechaCorrida"
            from resultados_blast where id_secuenciacion = %s order by ranking
            """,
            (id_secuenciacion,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------------------
# El trabajo en segundo plano
# --------------------------------------------------------------------------------------

def _correr_blast(id_corrida: str, id_secuenciacion: str, id_archivo: str, parametros: dict) -> None:
    """Envía, espera y persiste. Cualquier fallo deja la corrida en 'fallida' con motivo."""
    try:
        _actualizar(id_corrida, estado="en_curso", progreso="Leyendo la secuencia del archivo")
        lectura = ejecutar("leer_secuencia", {"idArchivo": id_archivo})

        _actualizar(id_corrida, progreso="Enviando la consulta a NCBI BLAST")
        envio = ejecutar("blast_enviar", {
            "secuencia": lectura["secuencia"],
            "baseDatos": parametros.get("baseDatos") or "",
            "maxHits": parametros.get("maxHits") or 10,
        })
        rid = envio["rid"]
        _actualizar(
            id_corrida, referencia_externa=rid,
            progreso=f"BLAST en curso (RID {rid}); NCBI estima {envio['esperaEstimadaS']} s",
        )

        limite = time.monotonic() + ESPERA_MAX_S
        while True:
            time.sleep(INTERVALO_S)
            estado = ejecutar("blast_resultado", {"rid": rid})
            if estado["estado"] != "en_curso":
                break
            restante = int(limite - time.monotonic())
            if restante <= 0:
                _actualizar(
                    id_corrida, estado="fallida",
                    error=f"BLAST superó el tiempo máximo de espera ({ESPERA_MAX_S // 60} min). "
                          f"El trabajo {rid} puede seguir vivo en NCBI.",
                )
                return
            _actualizar(id_corrida, progreso=f"BLAST en curso; quedan hasta {restante // 60} min de espera")

        if estado["estado"] == "sin_hits":
            _actualizar(
                id_corrida, estado="completada", progreso="Sin coincidencias",
                resultado={"rid": rid, "hits": [], "mensaje": "BLAST no encontró coincidencias."},
            )
            return

        hits = estado.get("hits") or []
        _actualizar(id_corrida, progreso=f"Guardando {len(hits)} coincidencias")
        guardar_hits_blast(id_secuenciacion, rid, envio["baseDatos"], hits)
        guardar_evidencia(
            tipo="blast", fuente="ncbi", id_secuenciacion=id_secuenciacion,
            titulo=f"BLAST {envio['baseDatos']} · {len(hits)} coincidencias",
            url=f"https://blast.ncbi.nlm.nih.gov/Blast.cgi?CMD=Get&RID={rid}",
            contenido={"rid": rid, "baseDatos": envio["baseDatos"], "consulta": lectura["encabezado"], "hits": hits},
        )

        # La taxonomía del hit principal se resuelve contra NCBI, no se infiere.
        taxonomia = None
        if hits and hits[0].get("taxid"):
            _actualizar(id_corrida, progreso="Resolviendo la taxonomía del hit principal")
            try:
                taxonomia = ejecutar("resolver_taxonomia", {"taxid": hits[0]["taxid"]})
                guardar_evidencia(
                    tipo="ncbi_taxonomy", fuente="ncbi", id_secuenciacion=id_secuenciacion,
                    accession=hits[0].get("accession"), titulo=taxonomia["nombreCientifico"],
                    contenido=taxonomia,
                )
            except ToolError:
                taxonomia = None  # el linaje es un extra; su ausencia no invalida el BLAST

        _actualizar(
            id_corrida, estado="completada", progreso="Completada",
            resultado={
                "rid": rid,
                "baseDatos": envio["baseDatos"],
                "consulta": lectura["encabezado"],
                "consultaLongitudPb": lectura["longitudPb"],
                "totalHits": len(hits),
                "hits": hits,
                "taxonomia": taxonomia,
            },
        )
    except ToolError as e:
        _actualizar(id_corrida, estado="fallida", error=str(e))
    except Exception as e:  # noqa: BLE001 — el hilo nunca debe morir en silencio
        _actualizar(id_corrida, estado="fallida", error=f"Error inesperado: {e}")


def _correr_arbol(id_corrida: str, id_secuenciacion: str, id_archivo: str) -> None:
    """Descarga las referencias de los hits y arma el árbol orientativo."""
    try:
        _actualizar(id_corrida, estado="en_curso", progreso="Leyendo la secuencia de consulta")
        lectura = ejecutar("leer_secuencia", {"idArchivo": id_archivo})

        hits = list_hits(id_secuenciacion)
        if len(hits) < 2:
            _actualizar(
                id_corrida, estado="fallida",
                error="Hacen falta al menos dos coincidencias de BLAST. Corre primero el análisis.",
            )
            return

        _actualizar(id_corrida, progreso=f"Descargando {min(len(hits), 8)} secuencias de referencia de NCBI")
        arbol = ejecutar("construir_arbol", {
            "consulta": lectura["secuencia"],
            "etiquetaConsulta": lectura["codigoSecuenciacion"],
            "accessions": [h["accession"] for h in hits if h.get("accession")][:8],
        })
        guardar_evidencia(
            tipo="ncbi_nuccore", fuente="ncbi", id_secuenciacion=id_secuenciacion,
            titulo=f"Referencias del árbol ({len(arbol['referencias'])} secuencias)",
            contenido={"referencias": arbol["referencias"], "newick": arbol["newick"]},
        )
        _actualizar(id_corrida, estado="completada", progreso="Árbol construido", resultado=arbol)
    except ToolError as e:
        _actualizar(id_corrida, estado="fallida", error=str(e))
    except Exception as e:  # noqa: BLE001
        _actualizar(id_corrida, estado="fallida", error=f"Error inesperado: {e}")


def lanzar_arbol(id_secuenciacion: str, id_archivo: str, quien: str) -> dict:
    corrida = crear_corrida(
        tipo="arbol_filogenetico", herramienta="construir_arbol",
        id_secuenciacion=id_secuenciacion, id_archivo=id_archivo,
        parametros={}, ejecutada_por=quien,
    )
    threading.Thread(
        target=_correr_arbol,
        args=(corrida["id"], id_secuenciacion, id_archivo),
        daemon=True,
        name=f"arbol-{corrida['id'][:8]}",
    ).start()
    return corrida


def lanzar_blast(id_secuenciacion: str, id_archivo: str, parametros: dict, quien: str) -> dict:
    corrida = crear_corrida(
        tipo="identificacion_taxonomica", herramienta="blast_enviar",
        id_secuenciacion=id_secuenciacion, id_archivo=id_archivo,
        parametros=parametros, ejecutada_por=quien,
    )
    hilo = threading.Thread(
        target=_correr_blast,
        args=(corrida["id"], id_secuenciacion, id_archivo, parametros),
        daemon=True,
        name=f"blast-{corrida['id'][:8]}",
    )
    hilo.start()
    return corrida
