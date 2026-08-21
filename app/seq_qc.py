"""Validación y control de calidad de secuencias — determinista, sin IA.

Esta es la primera etapa del análisis automatizado. A propósito **no** usa el modelo
generativo: comprobar que un FASTA es un FASTA, contar bases y calcular %GC son hechos
objetivos. La IA generativa entra después, para interpretar y redactar, y solo con
estas métricas ya calculadas como contexto.

FASTQ y FASTA no son lo mismo y aquí se tratan distinto:

    FASTQ  ->  lecturas crudas del secuenciador: identificador + bases + calidad Phred
    FASTA  ->  secuencia ya procesada (consenso o contigs): identificador + bases

Los archivos grandes se muestrean (`MAX_READS`): el objetivo es un QC orientativo en
segundos, no un FastQC completo.
"""
from __future__ import annotations

import gzip
import statistics

# Techo de lecturas que se analizan de un FASTQ. Un MiSeq entrega millones; con las
# primeras 200 mil el promedio de calidad ya es estable y la respuesta es inmediata.
MAX_READS = 200_000

# Phred+33 (Illumina 1.8+ y prácticamente todo lo actual).
PHRED_OFFSET = 33

BASES_ADN = set("ACGT")
# Códigos IUPAC de ambigüedad admitidos en una secuencia de ADN válida.
BASES_AMBIGUAS = set("NRYKMSWBDHV")
BASES_VALIDAS = BASES_ADN | BASES_AMBIGUAS | {"U", "-", "."}


class SecuenciaInvalida(ValueError):
    """El archivo no es un FASTA/FASTQ utilizable."""


def descomprimir(raw: bytes) -> tuple[str, bool]:
    """Devuelve (texto, venia_comprimido). Acepta .gz porque así llega un FASTQ real."""
    comprimido = raw[:2] == b"\x1f\x8b"
    data = gzip.decompress(raw) if comprimido else raw
    try:
        texto = data.decode("utf-8")
    except UnicodeDecodeError:
        texto = data.decode("latin-1")
    return texto, comprimido


def detectar_formato(texto: str, nombre_archivo: str = "") -> str:
    """Formato real por contenido; el nombre del archivo solo desempata."""
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        if linea.startswith(">"):
            return "fasta"
        if linea.startswith("@"):
            return "fastq"
        break
    nombre = nombre_archivo.lower()
    if nombre.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz")):
        return "fastq"
    if nombre.endswith((".fasta", ".fa", ".fna", ".fas", ".fasta.gz")):
        return "fasta"
    raise SecuenciaInvalida(
        "El archivo no empieza con '>' (FASTA) ni con '@' (FASTQ); no parece una secuencia."
    )


# --------------------------------------------------------------------------------------
# FASTA
# --------------------------------------------------------------------------------------

def _n50(longitudes: list[int]) -> int:
    """Longitud del contig en la que se acumula la mitad del ensamblaje."""
    if not longitudes:
        return 0
    total = sum(longitudes)
    acumulado = 0
    for largo in sorted(longitudes, reverse=True):
        acumulado += largo
        if acumulado >= total / 2:
            return largo
    return longitudes[0]


def analizar_fasta(texto: str) -> dict:
    encabezados: list[str] = []
    longitudes: list[int] = []
    conteo: dict[str, int] = {}
    invalidos: dict[str, int] = {}
    actual = 0
    abierto = False

    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith(">"):
            if abierto:
                longitudes.append(actual)
            encabezados.append(linea[1:].strip())
            actual = 0
            abierto = True
            continue
        if not abierto:
            raise SecuenciaInvalida("Hay bases antes del primer encabezado '>'.")
        for base in linea.upper():
            actual += 1
            if base in BASES_VALIDAS:
                conteo[base] = conteo.get(base, 0) + 1
            else:
                invalidos[base] = invalidos.get(base, 0) + 1
    if abierto:
        longitudes.append(actual)

    if not encabezados:
        raise SecuenciaInvalida("No se encontró ningún encabezado FASTA ('>').")
    total_bases = sum(longitudes)
    if total_bases == 0:
        raise SecuenciaInvalida("El archivo tiene encabezados pero ninguna base.")

    gc = conteo.get("G", 0) + conteo.get("C", 0)
    ambiguas = sum(conteo.get(b, 0) for b in BASES_AMBIGUAS)

    return {
        "formato": "fasta",
        "numSecuencias": len(encabezados),
        "encabezados": encabezados[:10],
        "longitudTotalPb": total_bases,
        "longitudMinPb": min(longitudes),
        "longitudMaxPb": max(longitudes),
        "longitudMediaPb": round(total_bases / len(longitudes), 1),
        "n50Pb": _n50(longitudes),
        "gcPct": round(100 * gc / total_bases, 2),
        "basesAmbiguas": ambiguas,
        "basesAmbiguasPct": round(100 * ambiguas / total_bases, 4),
        "caracteresInvalidos": sum(invalidos.values()),
        "detalleInvalidos": dict(sorted(invalidos.items())[:10]),
    }


def semaforo_fasta(m: dict) -> tuple[str, list[str]]:
    """Reglas explícitas y auditables. Nada de esto lo decide un modelo."""
    hallazgos: list[str] = []
    estado = "apta"

    if m["caracteresInvalidos"] > 0:
        hallazgos.append(
            f"{m['caracteresInvalidos']} caracteres fuera del alfabeto de ADN "
            f"({', '.join(m['detalleInvalidos'])})."
        )
        estado = "insuficiente"
    if m["longitudTotalPb"] < 200:
        hallazgos.append("Secuencia muy corta (<200 pb): BLAST dará resultados poco específicos.")
        estado = "insuficiente"
    elif m["longitudTotalPb"] < 500:
        hallazgos.append("Secuencia corta (<500 pb) para identificación taxonómica confiable.")
        estado = "revisar" if estado == "apta" else estado
    if m["basesAmbiguasPct"] > 5:
        hallazgos.append(f"{m['basesAmbiguasPct']}% de bases ambiguas (N): supera el 5%.")
        estado = "insuficiente"
    elif m["basesAmbiguasPct"] > 1:
        hallazgos.append(f"{m['basesAmbiguasPct']}% de bases ambiguas (N).")
        estado = "revisar" if estado == "apta" else estado
    if not 25 <= m["gcPct"] <= 75:
        hallazgos.append(f"%GC atípico ({m['gcPct']}%) para una bacteria.")
        estado = "revisar" if estado == "apta" else estado
    if m["numSecuencias"] > 1:
        hallazgos.append(
            f"El archivo tiene {m['numSecuencias']} secuencias; el análisis usará la primera "
            "salvo que se indique lo contrario."
        )

    if not hallazgos:
        hallazgos.append("Sin observaciones: la secuencia cumple los criterios básicos.")
    return estado, hallazgos


# --------------------------------------------------------------------------------------
# FASTQ
# --------------------------------------------------------------------------------------

def analizar_fastq(texto: str) -> dict:
    lineas = texto.splitlines()
    total_lineas = len(lineas)
    n_reads = 0
    truncado = False
    longitudes: list[int] = []
    suma_q = 0
    bases_totales = 0
    bases_q20 = 0
    bases_q30 = 0
    gc = 0
    ambiguas = 0
    q_min = 1000
    q_max = -1000
    calidades_por_read: list[float] = []

    i = 0
    while i + 3 < total_lineas + 1 and i < total_lineas:
        cabecera = lineas[i].rstrip()
        if not cabecera:
            i += 1
            continue
        if not cabecera.startswith("@"):
            raise SecuenciaInvalida(
                f"Línea {i + 1}: se esperaba un identificador de lectura que empiece con '@'."
            )
        if i + 3 >= total_lineas:
            raise SecuenciaInvalida("El archivo termina con una lectura incompleta (faltan líneas).")
        secuencia = lineas[i + 1].strip().upper()
        separador = lineas[i + 2].strip()
        calidad = lineas[i + 3].rstrip()
        if not separador.startswith("+"):
            raise SecuenciaInvalida(f"Línea {i + 3}: se esperaba el separador '+' del formato FASTQ.")
        if len(secuencia) != len(calidad):
            raise SecuenciaInvalida(
                f"Lectura {n_reads + 1}: la secuencia ({len(secuencia)} bases) y la calidad "
                f"({len(calidad)} valores) no coinciden."
            )

        n_reads += 1
        longitudes.append(len(secuencia))
        for base in secuencia:
            if base in ("G", "C"):
                gc += 1
            elif base in BASES_AMBIGUAS:
                ambiguas += 1
        suma_read = 0
        for caracter in calidad:
            q = ord(caracter) - PHRED_OFFSET
            suma_read += q
            q_min = min(q_min, q)
            q_max = max(q_max, q)
            if q >= 30:
                bases_q30 += 1
                bases_q20 += 1
            elif q >= 20:
                bases_q20 += 1
        suma_q += suma_read
        bases_totales += len(calidad)
        if calidad:
            calidades_por_read.append(suma_read / len(calidad))

        i += 4
        if n_reads >= MAX_READS:
            truncado = True
            break

    if n_reads == 0:
        raise SecuenciaInvalida("No se encontró ninguna lectura FASTQ completa.")

    return {
        "formato": "fastq",
        "numLecturas": n_reads,
        "muestreado": truncado,
        "basesTotales": bases_totales,
        "longitudMinPb": min(longitudes),
        "longitudMaxPb": max(longitudes),
        "longitudMediaPb": round(statistics.fmean(longitudes), 1),
        "calidadPromedio": round(suma_q / bases_totales, 2),
        "calidadMedianaPorLectura": round(statistics.median(calidades_por_read), 2),
        "calidadMin": q_min,
        "calidadMax": q_max,
        "pctQ20": round(100 * bases_q20 / bases_totales, 2),
        "pctQ30": round(100 * bases_q30 / bases_totales, 2),
        "gcPct": round(100 * gc / bases_totales, 2),
        "basesAmbiguas": ambiguas,
        "basesAmbiguasPct": round(100 * ambiguas / bases_totales, 4),
        "codificacionCalidad": f"Phred+{PHRED_OFFSET}",
    }


def semaforo_fastq(m: dict) -> tuple[str, list[str]]:
    hallazgos: list[str] = []
    estado = "apta"

    if m["calidadPromedio"] < 20:
        hallazgos.append(f"Calidad promedio Q{m['calidadPromedio']}: por debajo de Q20.")
        estado = "insuficiente"
    elif m["calidadPromedio"] < 28:
        hallazgos.append(f"Calidad promedio Q{m['calidadPromedio']}: aceptable pero mejorable.")
        estado = "revisar"
    if m["pctQ30"] < 50:
        hallazgos.append(f"Solo {m['pctQ30']}% de las bases alcanzan Q30.")
        estado = "insuficiente"
    elif m["pctQ30"] < 75:
        hallazgos.append(f"{m['pctQ30']}% de las bases alcanzan Q30 (lo esperado en MiSeq es >75%).")
        estado = "revisar" if estado == "apta" else estado
    if m["longitudMediaPb"] < 75:
        hallazgos.append(f"Lecturas cortas (media {m['longitudMediaPb']} pb).")
        estado = "revisar" if estado == "apta" else estado
    if m["basesAmbiguasPct"] > 1:
        hallazgos.append(f"{m['basesAmbiguasPct']}% de bases ambiguas (N).")
        estado = "revisar" if estado == "apta" else estado
    if m["muestreado"]:
        hallazgos.append(
            f"QC calculado sobre las primeras {m['numLecturas']:,} lecturas del archivo."
        )

    if not hallazgos:
        hallazgos.append("Sin observaciones: las lecturas cumplen los criterios básicos.")
    return estado, hallazgos


# --------------------------------------------------------------------------------------
# Entrada única
# --------------------------------------------------------------------------------------

def validar(raw: bytes, nombre_archivo: str = "") -> dict:
    """Valida y analiza un archivo de secuencia.

    Devuelve siempre la misma forma: formato, si es válido, semáforo, métricas y hallazgos.
    Un archivo inválido no revienta la petición: se guarda con el motivo, porque para la
    científica es información útil ("este archivo no sirve y por esto").
    """
    if not raw:
        return {
            "formato": None, "valido": False, "comprimido": False, "semaforo": "insuficiente",
            "metricas": {}, "hallazgos": ["El archivo está vacío."],
        }
    try:
        texto, comprimido = descomprimir(raw)
        formato = detectar_formato(texto, nombre_archivo)
        if formato == "fasta":
            metricas = analizar_fasta(texto)
            semaforo, hallazgos = semaforo_fasta(metricas)
        else:
            metricas = analizar_fastq(texto)
            semaforo, hallazgos = semaforo_fastq(metricas)
    except SecuenciaInvalida as e:
        return {
            "formato": None, "valido": False, "comprimido": raw[:2] == b"\x1f\x8b",
            "semaforo": "insuficiente", "metricas": {}, "hallazgos": [str(e)],
        }
    except (OSError, EOFError) as e:  # gzip corrupto
        return {
            "formato": None, "valido": False, "comprimido": True, "semaforo": "insuficiente",
            "metricas": {}, "hallazgos": [f"No se pudo descomprimir el archivo: {e}"],
        }

    return {
        "formato": formato,
        "valido": True,
        "comprimido": comprimido,
        "semaforo": semaforo,
        "metricas": metricas,
        "hallazgos": hallazgos,
    }


def extraer_primera_secuencia(texto: str, max_pb: int = 100_000) -> tuple[str, str]:
    """Devuelve (encabezado, secuencia) de la primera entrada de un FASTA.

    Es lo que se enviará a BLAST en la siguiente fase; se recorta para no mandar
    un genoma completo en una consulta interactiva.
    """
    encabezado = ""
    partes: list[str] = []
    visto = False
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if linea.startswith(">"):
            if visto:
                break
            encabezado = linea[1:].strip()
            visto = True
            continue
        if visto:
            partes.append(linea.upper())
            if sum(len(p) for p in partes) >= max_pb:
                break
    return encabezado, "".join(partes)[:max_pb]
