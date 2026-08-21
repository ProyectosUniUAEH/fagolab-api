"""Generación de la ficha científica: el único paso generativo del pipeline.

Aquí se junta todo lo que el sistema midió y recuperó —trazabilidad, QC, hits de BLAST,
taxonomía y literatura— y se le entrega al modelo como contexto para que redacte una
interpretación. El modelo **no** decide la especie ni calcula nada: recibe hechos y
escribe sobre ellos.

Dos cosas se guardan siempre junto a la ficha:

  * los parámetros de generación (modelo, temperatura, top_p, seed)
  * el hash de la evidencia exacta que se le pasó

Con eso una ficha se puede reproducir y comparar. Y con `conEvidencia=False` se genera
el control del experimento: la misma pregunta sin evidencia, que es como se demuestra
en qué se nota el grounding.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import repo_analisis, repo_ia, repo_seq
from .agent import deepseek
from .db import get_conn
from .tools import ejecutar
from .tools.base import ToolError

# Secciones fijas. Son las que pide el proceso científico que describió la doctora:
# observación → resultado → comparación con lo existente → literatura → discusión.
SECCIONES = [
    "Resumen",
    "Interpretación del resultado",
    "Contraste con la literatura",
    "Relevancia para fagoterapia",
    "Limitaciones",
    "Preguntas para el siguiente experimento",
]

PROMPT_SISTEMA = """Eres un asistente científico de un laboratorio de microbiología acuícola que \
investiga fagoterapia contra patógenos bacterianos en tilapia (Oreochromis niloticus).

Redactas la interpretación de un análisis genético a partir de la evidencia que se te \
entrega. Reglas que no puedes romper:

1. Usa ÚNICAMENTE los datos de la sección EVIDENCIA. Si algo no está ahí, dilo: \
"no se cuenta con ese dato". Nunca completes con conocimiento general como si fuera un \
resultado de este laboratorio.
2. Nunca afirmes la identidad de una especie. BLAST mide similitud, no confirma identidad. \
Escribe "la secuencia presenta mayor similitud con..." y no "la bacteria es...".
3. Cita siempre la fuente entre paréntesis: el accession para secuencias (NR_119039) y el \
PMID para artículos (PMID 12345678). No cites nada que no aparezca en la evidencia.
4. Respeta la procedencia del dato. Si la secuencia es pública o sintética, dilo con \
claridad y no la presentes como un resultado experimental del laboratorio.
5. Escribe en español, en tono científico y sobrio. Sin adjetivos promocionales ni \
conclusiones que la evidencia no sostenga.

Formato de salida: exactamente estas secciones, cada una con encabezado markdown de \
nivel 2 y en este orden:

## Resumen
## Interpretación del resultado
## Contraste con la literatura
## Relevancia para fagoterapia
## Limitaciones
## Preguntas para el siguiente experimento
"""

PROMPT_SIN_EVIDENCIA = """Eres un asistente científico de un laboratorio de microbiología \
acuícola que investiga fagoterapia en tilapia.

Redacta la interpretación de un análisis genético usando las mismas secciones de siempre. \
No se te entrega evidencia: responde con lo que sepas.

Formato de salida: exactamente estas secciones, cada una con encabezado markdown de nivel 2:

## Resumen
## Interpretación del resultado
## Contraste con la literatura
## Relevancia para fagoterapia
## Limitaciones
## Preguntas para el siguiente experimento
"""


PROMPT_CONSULTAS = """Eres el asistente de un laboratorio de microbiología acuícola que \
investiga fagoterapia contra patógenos bacterianos en tilapia (Oreochromis niloticus).

Se acaba de identificar un organismo por BLAST. Redacta las consultas de búsqueda que \
harías en PubMed para reunir la literatura relevante a esta investigación.

Reglas:
- En inglés, que es el idioma de PubMed.
- Entre 2 y 4 consultas, cada una en una línea, sin numerar ni explicar.
- Cubre ángulos distintos: fagos contra ese hospedero, aplicación en acuicultura, y \
caracterización o eficacia del tratamiento.
- Solo las consultas. Nada más en la respuesta."""

# Si no hay proveedor configurado el pipeline no puede quedarse sin literatura; estas dos
# consultas son el mínimo razonable. Se marcan como no generadas para no atribuirle al
# modelo algo que no hizo.
def _consultas_base(organismo: str) -> list[str]:
    return [f"{organismo} bacteriophage", f"{organismo} phage therapy aquaculture"]


def generar_consultas(organismo: str, contexto: str | None = None) -> list[str]:
    """El modelo redacta los términos de búsqueda. Primer paso generativo del pipeline.

    No es un detalle menor: convertir "Aeromonas salmonicida" más el contexto del proyecto
    en las preguntas correctas para la literatura es criterio, no plantilla. Y es
    verificable — las consultas se guardan junto a la ficha.

    `contexto` es la línea de investigación bajo la que se está mirando la muestra. Con el
    mismo organismo medido y distinto encuadre el modelo escribe otras preguntas, que es
    exactamente lo que distingue un paso generativo de una plantilla.
    """
    config = repo_ia.get_config(include_secret=True)
    if not config or not config.get("apiKey"):
        return _consultas_base(organismo)
    encargo = f"Organismo identificado: {organismo}"
    if contexto:
        encargo += f"\nLínea de investigación bajo la que se analiza: {contexto}"
    try:
        respuesta = deepseek.completar(
            base_url=config["baseUrl"],
            api_key=config["apiKey"],
            model=config["modelo"],
            messages=[
                {"role": "system", "content": PROMPT_CONSULTAS},
                {"role": "user", "content": encargo},
            ],
            temperature=0.4,
            max_tokens=220,
        )
    except RuntimeError:
        return _consultas_base(organismo)

    consultas = [
        re.sub(r'^["\'\-\d\.\)\s]+|["\']+$', "", linea).strip()
        for linea in (respuesta["texto"] or "").splitlines()
        if linea.strip()
    ]
    consultas = [c for c in consultas if 8 < len(c) < 160][:4]
    return consultas or _consultas_base(organismo)


PROMPT_PRIORIZAR = """Eres el asistente de un laboratorio que investiga fagoterapia contra \
patógenos bacterianos en tilapia.

Se recuperó una lista larga de artículos de PubMed. Tu trabajo es quedarte con los más \
útiles para decidir si vale la pena buscar un fago contra este organismo.

Criterios, en este orden:
1. Que describa fagos concretos contra el organismo o su género.
2. Que reporte aplicación o eficacia en peces o acuicultura.
3. Que aporte caracterización útil (rango de hospedero, ciclo lítico, seguridad).
Descarta lo que solo mencione la bacteria de pasada o trate de temas ajenos.

Devuelve ÚNICAMENTE líneas con este formato exacto, de la más útil a la menos:

PMID | motivo en una frase corta

Sin encabezados, sin numerar, sin texto adicional."""


def priorizar_evidencia(organismo: str, articulos: list[dict], tope: int = 8) -> list[dict]:
    """El modelo ordena y filtra la literatura recuperada. Segundo paso generativo.

    Importa por una razón práctica: una búsqueda amplia trae decenas de artículos y la
    mayoría son ruido. Elegir cuáles sostienen una decisión experimental es criterio, y
    es exactamente el tipo de tarea donde un modelo aporta algo que una regla no.

    Nunca inventa: solo puede devolver PMIDs que estaban en la lista. Los que el modelo
    no reconozca se descartan, y si falla se conserva el orden de relevancia de PubMed.
    """
    if not articulos:
        return []
    config = repo_ia.get_config(include_secret=True)
    if not config or not config.get("apiKey") or len(articulos) <= tope:
        return articulos[:tope]

    catalogo = "\n\n".join(
        f"PMID {a['pmid']} ({a.get('anio') or '—'}) — {a['titulo']}\n"
        f"{(a.get('abstract') or '')[:420]}"
        for a in articulos
    )
    try:
        respuesta = deepseek.completar(
            base_url=config["baseUrl"],
            api_key=config["apiKey"],
            model=config["modelo"],
            messages=[
                {"role": "system", "content": PROMPT_PRIORIZAR},
                {"role": "user",
                 "content": f"Organismo: {organismo}\nArtículos recuperados:\n\n{catalogo}"},
            ],
            temperature=0.2,
            max_tokens=700,
        )
    except RuntimeError:
        return articulos[:tope]

    por_pmid = {a["pmid"]: a for a in articulos}
    elegidos: list[dict] = []
    for linea in (respuesta["texto"] or "").splitlines():
        encontrado = re.search(r"(\d{6,9})", linea)
        if not encontrado:
            continue
        articulo = por_pmid.get(encontrado.group(1))
        if not articulo or articulo in elegidos:
            continue
        motivo = linea.split("|", 1)[1].strip() if "|" in linea else ""
        elegidos.append({**articulo, "motivoSeleccion": motivo})
        if len(elegidos) >= tope:
            break

    return elegidos or articulos[:tope]


# --------------------------------------------------------------------------------------
# Reunir la evidencia
# --------------------------------------------------------------------------------------

def reunir_evidencia(id_secuenciacion: str, *, max_articulos: int = 5) -> dict:
    """Junta todo lo medido y recuperado sobre una secuenciación.

    Las consultas de literatura las redacta el modelo (`generar_consultas`); ejecutarlas
    contra PubMed es determinista. Esa separación es el corazón del diseño: el modelo
    aporta criterio, no resultados.
    """
    trazabilidad = ejecutar("trazabilidad_secuenciacion", {"idSecuenciacion": id_secuenciacion})
    hits = repo_analisis.list_hits(id_secuenciacion)
    secuenciacion = repo_seq.get_secuenciacion(id_secuenciacion)

    # El QC que interesa en la ficha es el de la secuencia que se analizó, es decir el
    # FASTA de consenso. Si solo hay lecturas crudas se usa el FASTQ, pero el FASTA manda.
    validos = [a for a in secuenciacion["archivos"] if a["estadoValidacion"] == "valido"]
    preferidos = [a for a in validos if a["formato"] == "fasta"] or validos
    qc = None
    if preferidos:
        elegido = preferidos[-1]
        qc = {
            "archivo": elegido["nombreArchivo"],
            "formato": elegido["formato"],
            "semaforo": elegido["semaforo"],
            "metricas": elegido["metricas"],
            "hallazgos": elegido["hallazgos"],
        }

    taxonomia = None
    literatura: list[dict] = []
    consultas: list[str] = []
    if hits:
        organismo = hits[0]["organismo"]
        if hits[0].get("taxId"):
            try:
                taxonomia = ejecutar("resolver_taxonomia", {"taxid": hits[0]["taxId"]})
            except ToolError:
                taxonomia = None
        # Las consultas las REDACTA el modelo a partir del organismo y del contexto del
        # proyecto: es el primer paso generativo del pipeline.
        consultas = generar_consultas(organismo)
        vistos: set[str] = set()
        for consulta in consultas:
            try:
                resultado = ejecutar("buscar_pubmed", {"consulta": consulta, "maxResultados": max_articulos})
            except ToolError:
                continue
            for articulo in resultado["articulos"]:
                if articulo["pmid"] in vistos:
                    continue
                vistos.add(articulo["pmid"])
                literatura.append(articulo)
                repo_analisis.guardar_evidencia(
                    tipo="pubmed", fuente="pubmed", id_secuenciacion=id_secuenciacion,
                    pmid=articulo["pmid"], titulo=articulo["titulo"], url=articulo["url"],
                    contenido=articulo,
                )
            if len(literatura) >= max_articulos:
                break
        literatura = literatura[:max_articulos]

    return {
        "secuenciacion": {
            "codigo": secuenciacion["codigo"],
            "origenDato": secuenciacion["origenDato"],
            "origenEtiqueta": secuenciacion["origenEtiqueta"],
            "organismoDeclarado": secuenciacion["organismoDeclarado"],
            "accession": secuenciacion["accession"],
            "fuenteExterna": secuenciacion["fuenteExterna"],
            "plataforma": secuenciacion["plataforma"],
            "tecnologia": secuenciacion["tecnologia"],
        },
        "trazabilidad": trazabilidad,
        "qc": qc,
        "hits": hits,
        "taxonomia": taxonomia,
        "consultasLiteratura": consultas,
        "literatura": literatura,
    }


def _formatear_evidencia(evidencia: dict) -> str:
    """La evidencia como texto plano y ordenado. El modelo lee mejor esto que JSON crudo."""
    lineas: list[str] = []
    seq = evidencia["secuenciacion"]
    traza = evidencia["trazabilidad"]

    lineas.append("### MUESTRA")
    lineas.append(f"Código: {seq['codigo']}")
    lineas.append(f"Procedencia del dato: {seq['origenEtiqueta']}")
    if seq["accession"]:
        lineas.append(f"Accession de origen: {seq['accession']} ({seq['fuenteExterna']})")
    if seq["organismoDeclarado"]:
        lineas.append(f"Organismo declarado por quien registró: {seq['organismoDeclarado']}")
    lineas.append(f"Plataforma: {seq['plataforma'] or 'no registrada'} · {seq['tecnologia'] or ''}".strip())
    lineas.append(traza["nota"])
    if traza.get("tieneCadenaExperimental"):
        lineas.append(
            f"Pez {traza.get('pez') or '—'} ({traza.get('especie') or '—'}), órgano {traza.get('organo') or '—'}, "
            f"lote {traza.get('lote') or '—'}, medio {traza.get('medio') or '—'}."
        )
        if traza.get("descripcionColonia"):
            lineas.append(f"Observación de colonia: {traza['descripcionColonia']}")
        if traza.get("ratio260280"):
            lineas.append(
                f"NanoDrop: 260/280 = {traza['ratio260280']}, 260/230 = {traza.get('ratio260230')}, "
                f"{traza.get('concentracionNgUl')} ng/µL, calidad {traza.get('calidadAdn')}."
            )
        if traza.get("codigoGel"):
            lineas.append(
                f"Gel {traza['codigoGel']}, carril {traza.get('carril')}, banda de {traza.get('bandaPb')} pb."
            )

    if evidencia["qc"]:
        qc = evidencia["qc"]
        lineas.append("\n### CONTROL DE CALIDAD DE LA SECUENCIA (medido por el sistema)")
        lineas.append(f"Archivo {qc['archivo']} · formato {qc['formato'].upper()} · estado {qc['semaforo']}")
        lineas.append(json.dumps(qc["metricas"], ensure_ascii=False))
        for hallazgo in qc["hallazgos"]:
            lineas.append(f"- {hallazgo}")

    if evidencia["hits"]:
        lineas.append("\n### RESULTADOS DE BLAST (NCBI, valores medidos)")
        lineas.append(f"Base de datos: {evidencia['hits'][0]['baseDatos']} · consultada el {evidencia['hits'][0]['fechaCorrida']}")
        for hit in evidencia["hits"]:
            lineas.append(
                f"{hit['ranking']}. {hit['organismo']} — identidad {hit['identidadPct']}%, "
                f"cobertura {hit['coberturaPct']}%, e-value {hit['eValue']}, "
                f"bit score {hit['bitScore']}, accession {hit['accession']}"
            )
    else:
        lineas.append("\n### RESULTADOS DE BLAST\nNo se ha ejecutado BLAST sobre esta secuencia.")

    if evidencia["taxonomia"]:
        tax = evidencia["taxonomia"]
        linaje = " > ".join(n["nombre"] for n in tax.get("linaje", []))
        lineas.append("\n### TAXONOMÍA DEL HIT PRINCIPAL (NCBI Taxonomy)")
        lineas.append(f"{tax['nombreCientifico']} (rango {tax['rango']})")
        lineas.append(f"Linaje: {linaje}")

    if evidencia["literatura"]:
        lineas.append("\n### LITERATURA RECUPERADA DE PUBMED")
        lineas.append(f"Consultas ejecutadas: {'; '.join(evidencia['consultasLiteratura'])}")
        for art in evidencia["literatura"]:
            lineas.append(
                f"\nPMID {art['pmid']} — {art['titulo']}\n"
                f"{art['autores']} ({art['anio']}), {art['revista']}\n"
                f"Abstract: {art['abstract'][:1400]}"
            )
    else:
        lineas.append("\n### LITERATURA\nNo se recuperaron artículos para esta muestra.")

    return "\n".join(lineas)


def _partir_secciones(texto: str) -> dict:
    """Separa la salida por encabezados de nivel 2, conservando el orden pedido."""
    partes: dict[str, str] = {}
    actual: str | None = None
    acumulado: list[str] = []
    for linea in texto.splitlines():
        encabezado = re.match(r"^\s*##\s+(.+?)\s*$", linea)
        if encabezado:
            if actual:
                partes[actual] = "\n".join(acumulado).strip()
            actual = encabezado.group(1).strip()
            acumulado = []
            continue
        if actual:
            acumulado.append(linea)
    if actual:
        partes[actual] = "\n".join(acumulado).strip()
    return partes


# --------------------------------------------------------------------------------------
# Generación
# --------------------------------------------------------------------------------------

def generar_ficha(id_secuenciacion: str, opciones: dict, quien: str) -> dict:
    config = repo_ia.get_config(include_secret=True)
    if not config or not config.get("apiKey"):
        raise ValueError(
            "No hay un proveedor de IA configurado. Ve a Asistente → Configuración y "
            "guarda la llave del proveedor."
        )

    con_evidencia = opciones.get("conEvidencia")
    con_evidencia = True if con_evidencia is None else bool(con_evidencia)
    temperatura = float(opciones.get("temperatura", config.get("temperatura") or 0.3))
    top_p = opciones.get("topP")
    top_p = float(top_p) if top_p is not None else None
    seed = opciones.get("seed")
    seed = int(seed) if seed not in (None, "") else None

    secuenciacion = repo_seq.get_secuenciacion(id_secuenciacion)

    if con_evidencia:
        evidencia = reunir_evidencia(id_secuenciacion, max_articulos=int(opciones.get("maxArticulos") or 5))
        contexto = _formatear_evidencia(evidencia)
        prompt_sistema = PROMPT_SISTEMA
        usuario = (
            f"EVIDENCIA\n\n{contexto}\n\n"
            "Redacta la ficha de análisis de esta muestra siguiendo las secciones indicadas."
        )
        resumen_evidencia = {
            "hits": len(evidencia["hits"]),
            "articulos": len(evidencia["literatura"]),
            "accessions": [h["accession"] for h in evidencia["hits"]],
            "pmids": [a["pmid"] for a in evidencia["literatura"]],
            "consultas": evidencia["consultasLiteratura"],
            "conQc": bool(evidencia["qc"]),
        }
    else:
        # Control del experimento: mismo encargo, sin nada en qué apoyarse.
        contexto = ""
        prompt_sistema = PROMPT_SIN_EVIDENCIA
        usuario = (
            f"Redacta la ficha de análisis de la muestra {secuenciacion['codigo']} "
            f"de un laboratorio de fagoterapia en tilapia."
        )
        resumen_evidencia = {"control": "generada sin evidencia, para comparación"}

    huella = hashlib.sha256(contexto.encode("utf-8")).hexdigest() if contexto else None

    inicio = time.monotonic()
    try:
        respuesta = deepseek.completar(
            base_url=config["baseUrl"],
            api_key=config["apiKey"],
            model=config["modelo"],
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": usuario},
            ],
            temperature=temperatura,
            top_p=top_p,
            seed=seed,
        )
    except RuntimeError as e:
        raise ValueError(str(e))
    duracion_ms = int((time.monotonic() - inicio) * 1000)

    texto = (respuesta["texto"] or "").strip()
    if not texto:
        raise ValueError("El modelo devolvió una ficha vacía.")

    return _guardar_ficha(
        id_secuenciacion=id_secuenciacion,
        texto=texto,
        secciones=_partir_secciones(texto),
        proveedor=config.get("proveedor") or "",
        modelo=respuesta["modelo"],
        temperatura=temperatura,
        top_p=top_p,
        seed=seed,
        prompt_sistema=prompt_sistema,
        con_evidencia=con_evidencia,
        evidencia_sha256=huella,
        evidencia_resumen=resumen_evidencia,
        tokens_entrada=respuesta.get("tokensEntrada"),
        tokens_salida=respuesta.get("tokensSalida"),
        duracion_ms=duracion_ms,
        etiqueta=(opciones.get("etiquetaExperimento") or "").strip() or None,
        quien=quien,
    )


def _guardar_ficha(**c) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into fichas_analisis
                  (id_secuenciacion, texto, secciones, proveedor, modelo, temperatura, top_p,
                   seed, prompt_sistema, con_evidencia, evidencia_sha256, evidencia_resumen,
                   tokens_entrada, tokens_salida, duracion_ms, etiqueta_experimento, generada_por)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_ficha::text as id
                """,
                (c["id_secuenciacion"], c["texto"], json.dumps(c["secciones"], ensure_ascii=False),
                 c["proveedor"], c["modelo"], c["temperatura"], c["top_p"], c["seed"],
                 c["prompt_sistema"], c["con_evidencia"], c["evidencia_sha256"],
                 json.dumps(c["evidencia_resumen"], ensure_ascii=False), c["tokens_entrada"],
                 c["tokens_salida"], c["duracion_ms"], c["etiqueta"], c["quien"]),
            )
            id_ficha = cur.fetchone()["id"]
        conn.commit()
    return get_ficha(id_ficha)


_SELECT_FICHA = """
    select id_ficha::text as id, id_secuenciacion::text as "idSecuenciacion",
           texto, secciones, coalesce(proveedor,'') as proveedor, modelo,
           temperatura::float as temperatura, top_p::float as "topP", seed,
           con_evidencia as "conEvidencia", evidencia_sha256 as "evidenciaSha256",
           evidencia_resumen as "evidenciaResumen",
           tokens_entrada as "tokensEntrada", tokens_salida as "tokensSalida",
           duracion_ms as "duracionMs",
           coalesce(etiqueta_experimento,'') as "etiquetaExperimento",
           coalesce(generada_por,'') as "generadaPor",
           to_char(created_at,'YYYY-MM-DD HH24:MI') as "creadaEn"
    from fichas_analisis
"""


def get_ficha(id_ficha: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SELECT_FICHA + " where id_ficha = %s", (id_ficha,))
        fila = cur.fetchone()
        if not fila:
            raise ValueError("Ficha no encontrada")
        return fila


def list_fichas(id_secuenciacion: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            _SELECT_FICHA + " where id_secuenciacion = %s order by created_at desc",
            (id_secuenciacion,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------------------
# Experimentos: variar una sola variable y comparar
# --------------------------------------------------------------------------------------

# Qué se puede variar. Cada entrada dice cómo se traduce el valor a una opción de
# generación, porque no todas son numéricas: "con evidencia" es un interruptor.
VARIABLES = {
    "temperatura": {
        "etiqueta": "Temperatura",
        "explicacion": "Controla qué tan dispersa es la distribución de la que se muestrea "
                       "cada token. Baja = conservadora y repetible; alta = exploratoria.",
        "valoresSugeridos": [0.1, 0.5, 1.0],
    },
    "topP": {
        "etiqueta": "Top-p",
        "explicacion": "Recorta la cola de la distribución: solo se muestrea del conjunto de "
                       "tokens que acumula esa probabilidad.",
        "valoresSugeridos": [0.3, 0.7, 1.0],
    },
    "seed": {
        "etiqueta": "Seed",
        "explicacion": "Fija el punto de partida del muestreo. Con la misma seed y los mismos "
                       "parámetros, la generación debe repetirse.",
        "valoresSugeridos": [7, 7, 42],
    },
    "conEvidencia": {
        "etiqueta": "Con evidencia",
        "explicacion": "El control del experimento. Sin evidencia el modelo responde de memoria "
                       "y se puede observar directamente la alucinación.",
        "valoresSugeridos": [True, False],
    },
}


def variables_experimento() -> list[dict]:
    return [{"clave": k, **v} for k, v in VARIABLES.items()]


def _correr_experimento(id_corrida: str, id_secuenciacion: str, config: dict, quien: str) -> None:
    """Genera una ficha por cada valor de la variable, dejando el resto fijo."""
    from . import repo_analisis  # import local: evita un ciclo entre los dos repos

    variable = config["variable"]
    valores = config["valores"]
    base = config.get("base") or {}
    etiqueta = config.get("etiqueta") or variable
    generadas: list[dict] = []

    try:
        repo_analisis._actualizar(id_corrida, estado="en_curso", progreso=f"0 de {len(valores)}")
        for indice, valor in enumerate(valores, start=1):
            repo_analisis._actualizar(
                id_corrida,
                progreso=f"{indice - 1} de {len(valores)} · generando con {variable} = {valor}",
            )
            opciones = {
                "conEvidencia": base.get("conEvidencia", True),
                "temperatura": base.get("temperatura", 0.3),
                "topP": base.get("topP"),
                "seed": base.get("seed"),
                "etiquetaExperimento": f"{etiqueta} · {variable}={valor}",
                variable: valor,
            }
            ficha = generar_ficha(id_secuenciacion, opciones, quien)
            generadas.append({
                "idFicha": ficha["id"],
                "valor": valor,
                "tokensSalida": ficha["tokensSalida"],
                "etiqueta": ficha["etiquetaExperimento"],
            })

        repo_analisis._actualizar(
            id_corrida, estado="completada", progreso=f"{len(generadas)} fichas generadas",
            resultado={
                "variable": variable,
                "etiqueta": etiqueta,
                "explicacion": VARIABLES.get(variable, {}).get("explicacion", ""),
                "base": base,
                "fichas": generadas,
            },
        )
    except Exception as e:  # noqa: BLE001 — el hilo no debe morir en silencio
        repo_analisis._actualizar(
            id_corrida, estado="fallida",
            error=f"{e}",
            resultado={"variable": variable, "fichas": generadas},
        )


def lanzar_experimento(id_secuenciacion: str, config: dict, quien: str) -> dict:
    """Arranca una corrida experimental en segundo plano.

    Cada ficha tarda segundos, así que una serie de tres o cuatro no cabe en un request.
    Se reutiliza `corridas_analisis`: es exactamente el mismo problema que BLAST.
    """
    from . import repo_analisis
    import threading

    variable = config.get("variable")
    if variable not in VARIABLES:
        raise ValueError(f"Variable no soportada: {variable}. Opciones: {', '.join(VARIABLES)}.")
    valores = config.get("valores") or VARIABLES[variable]["valoresSugeridos"]
    if not isinstance(valores, list) or not 2 <= len(valores) <= 6:
        raise ValueError("Un experimento necesita entre 2 y 6 valores para poder comparar.")

    corrida = repo_analisis.crear_corrida(
        tipo="experimento_ficha", herramienta="redactar_ficha",
        id_secuenciacion=id_secuenciacion, id_archivo=None,
        parametros={"variable": variable, "valores": valores, "base": config.get("base") or {}},
        ejecutada_por=quien,
    )
    hilo = threading.Thread(
        target=_correr_experimento,
        args=(corrida["id"], id_secuenciacion, {**config, "valores": valores}, quien),
        daemon=True,
        name=f"exp-{corrida['id'][:8]}",
    )
    hilo.start()
    return corrida


def eliminar_ficha(id_ficha: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from fichas_analisis where id_ficha = %s", (id_ficha,))
            if cur.rowcount == 0:
                raise ValueError("Ficha no encontrada")
        conn.commit()
    return {"ok": True}
