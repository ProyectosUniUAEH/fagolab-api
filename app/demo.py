"""Demostración guiada del pipeline, para ejecutar en terminal.

    py -3 -m app.demo                          corrida real contra NCBI, PubMed y el modelo
    py -3 -m app.demo --grabar corrida.json    igual, pero guarda todo lo que ocurrió
    py -3 -m app.demo --replay corrida.json    reproduce esa corrida con sus tiempos reales
    py -3 -m app.demo --replay corrida.json --rapido   la reproduce a 4x

Existe por una razón concreta: en la interfaz web el análisis se ve como una caja negra
que tarda y luego escupe un resultado. En la terminal se puede mostrar **paso por paso
quién hace qué**, que es justo lo que hay que explicar.

Cada etapa se anuncia con su naturaleza:

    DETERMINISTA   código clásico. Mismo insumo, mismo resultado, siempre.
    SERVICIO       consulta a una base pública externa (NCBI, PubMed).
    GENERATIVO     el modelo de lenguaje produce texto que no existía.

El modo replay no es maquillaje: reproduce una corrida real que quedó grabada, con sus
tiempos y sus salidas exactas. Se usa cuando NCBI está lento y no se puede depender de
él en vivo. La grabación lleva su fecha y se puede abrir y leer.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import threading
import time
from datetime import datetime

# --------------------------------------------------------------------------------------
# Presentación
# --------------------------------------------------------------------------------------

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "det": "\033[38;5;37m",    # verde azulado — determinista
    "gen": "\033[38;5;140m",   # violeta — generativo
    "ext": "\033[38;5;179m",   # ámbar — servicio externo
    "ok": "\033[38;5;42m", "err": "\033[38;5;203m", "gris": "\033[38;5;245m",
    "blanco": "\033[38;5;255m",
}

NATURALEZA = {
    "det": ("DETERMINISTA", "det"),
    "ext": ("SERVICIO EXTERNO", "ext"),
    "gen": ("IA GENERATIVA", "gen"),
}

DESCRIPCION_NATURALEZA = {
    "det": "código clásico · mismo insumo, mismo resultado",
    "ext": "consulta a una base pública (NCBI, PubMed)",
    "gen": "el modelo produce texto que no existía",
}

ANCHO = 74
TOTAL_PASOS = 9

# Estado del modo de ejecución. Lo fija main() a partir de los argumentos.
MODO = "vivo"            # vivo | replay
ESCALA = 1.0             # divisor de las esperas en replay
GRABACION: dict = {}     # lo leído en replay
REGISTRO: dict = {}      # lo que se va grabando en vivo
LOGS = True


def _activar_ansi() -> None:
    """En consolas de Windows hay que pedir el modo de secuencias de escape."""
    if os.name == "nt":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)
        except Exception:
            pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def c(texto: str, color: str) -> str:
    return f"{C[color]}{texto}{C['reset']}"


def _interactivo() -> bool:
    """¿Hay una terminal de verdad al otro lado? Decide animaciones y pausas."""
    return sys.stdout.isatty()


def _escribir(texto: str, retardo: float = 0.012) -> None:
    """Escritura carácter a carácter. Solo para los títulos: da ritmo a la apertura."""
    if not _interactivo():
        print(texto)
        return
    retardo = retardo / max(ESCALA, 1.0)
    for letra in texto:
        sys.stdout.write(letra)
        sys.stdout.flush()
        time.sleep(retardo)
    sys.stdout.write("\n")


def _envolver(texto: str, ancho: int) -> list[str]:
    palabras, lineas, actual = texto.split(), [], ""
    for palabra in palabras:
        if len(actual) + len(palabra) + 1 > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    return lineas


class Girador:
    """Indicador de actividad para las etapas que tardan."""

    CUADROS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, mensaje: str, color: str = "gris") -> None:
        self.mensaje = mensaje
        self.color = color
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self.inicio = 0.0

    def __enter__(self) -> "Girador":
        self.inicio = time.monotonic()
        if not _interactivo():
            return self
        self._hilo = threading.Thread(target=self._girar, daemon=True)
        self._hilo.start()
        return self

    def _girar(self) -> None:
        for cuadro in itertools.cycle(self.CUADROS):
            if self._parar.is_set():
                return
            transcurrido = time.monotonic() - self.inicio
            sys.stdout.write(
                f"\r   {c(cuadro, self.color)} {c(self.mensaje, 'gris')} "
                f"{c(f'{transcurrido:5.1f}s', 'dim')}   "
            )
            sys.stdout.flush()
            time.sleep(0.09)

    def actualizar(self, mensaje: str) -> None:
        self.mensaje = mensaje

    def __exit__(self, *_) -> None:
        self._parar.set()
        if self._hilo:
            self._hilo.join(timeout=0.5)
        sys.stdout.write("\r" + " " * (ANCHO + 20) + "\r")
        sys.stdout.flush()


def banner() -> None:
    print()
    print(c("╔" + "═" * ANCHO + "╗", "det"))
    for linea, tono in (
        ("  FagoLab · análisis genético asistido por IA generativa", "blanco"),
        ("  Proyecto de investigación UAEH · plataforma Software Factory", "gris"),
    ):
        print(c("║", "det") + c(linea.ljust(ANCHO), tono) + c("║", "det"))
    print(c("╚" + "═" * ANCHO + "╝", "det"))
    print()
    _escribir(c("  Cada paso declara quién lo ejecuta:", "gris"), 0.006)
    print()
    for clave in ("det", "ext", "gen"):
        etiqueta, color = NATURALEZA[clave]
        print(f"   {c('●', color)} {c(etiqueta.ljust(18), color)} "
              f"{c(DESCRIPCION_NATURALEZA[clave], 'gris')}")
    print()
    if MODO == "replay":
        sello = GRABACION.get("grabadaEn", "fecha desconocida")
        print(f"   {c('▸ REPRODUCCIÓN', 'ext')} {c('de una corrida real del ' + sello, 'gris')}")
        print(f"     {c('mismas salidas y mismos tiempos; nada se recalcula', 'dim')}")
        print()


def barra(numero: int) -> str:
    llenos = "▰" * numero
    vacios = "▱" * (TOTAL_PASOS - numero)
    return f"{c(llenos, 'det')}{c(vacios, 'dim')}"


def paso(numero: int, titulo: str, naturaleza: str, porque: str = "") -> None:
    etiqueta, color = NATURALEZA[naturaleza]
    print()
    marca = f" PASO {numero}/{TOTAL_PASOS} "
    relleno = ANCHO - len(marca) - len(etiqueta) - 5
    print(
        c("┌─", color) + c(marca, "bold") + c("─" * relleno, "dim")
        + f" {c('●', color)} " + c(etiqueta, color) + c("─┐", color)
    )
    print(c("│ ", color) + c(titulo, "blanco"))
    if porque:
        for linea in _envolver(porque, ANCHO - 4):
            print(c("│ ", color) + c(linea, "gris"))
    print(c("│ ", color) + barra(numero - 1))
    print(c("└" + "─" * (ANCHO + 1), "dim"))


def log(naturaleza: str, llamada: str, resultado: str, segundos: float) -> None:
    """Una línea por invocación real. Es la traza que un ingeniero querría ver."""
    if not LOGS:
        return
    etiqueta, color = NATURALEZA[naturaleza]
    marca = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    corta = {"det": "DET", "ext": "EXT", "gen": "GEN"}[naturaleza]
    print(
        f"   {c(marca, 'dim')} {c('│', 'dim')} {c(corta, color)} {c('│', 'dim')} "
        f"{c(llamada.ljust(34), 'gris')} {c('→', 'dim')} "
        f"{c(resultado, 'gris')} {c(f'{segundos:.2f}s', 'dim')}"
    )


def ok(texto: str, tiempo: float | None = None) -> None:
    reloj = c(f"  [{tiempo:.2f} s]", "dim") if tiempo is not None else ""
    print(f"   {c('✔', 'ok')} {texto}{reloj}")


def dato(etiqueta: str, valor: str) -> None:
    print(f"     {c(etiqueta.ljust(22), 'gris')} {c(valor, 'blanco')}")


def error(texto: str) -> None:
    print(f"   {c('✖', 'err')} {c(texto, 'err')}")


# --------------------------------------------------------------------------------------
# El puente entre corrida real y reproducción
# --------------------------------------------------------------------------------------

def etapa(clave: str, funcion_viva, *, minimo: float = 0.0):
    """Ejecuta un paso, o lo reproduce desde la grabación.

    Una sola ruta de renderizado para los dos modos: lo único que cambia es de dónde
    sale el valor. Así la demostración se ve idéntica corra en vivo o en reproducción,
    y no hay un segundo camino de código que pueda mentir.
    """
    if MODO == "replay":
        entrada = GRABACION.get("pasos", {}).get(clave)
        if entrada is None:
            raise RuntimeError(f"La grabación no contiene el paso '{clave}'.")
        espera = max(entrada.get("segundos", 0.0), minimo) / max(ESCALA, 0.001)
        time.sleep(min(espera, 25.0))
        return entrada["valor"], entrada.get("segundos", 0.0)

    inicio = time.monotonic()
    valor = funcion_viva()
    segundos = time.monotonic() - inicio
    REGISTRO[clave] = {"segundos": round(segundos, 3), "valor": valor}
    return valor, segundos


# --------------------------------------------------------------------------------------
# Configuración del experimento
# --------------------------------------------------------------------------------------

CONTEXTOS = [
    ("Fagoterapia en tilapia",
     "búsqueda de fagos líticos contra el patógeno aislado, aplicados en Oreochromis "
     "niloticus en cultivo intensivo"),
    ("Resistencia a antibióticos",
     "alternativas al uso de antibióticos en acuicultura y perfiles de resistencia del "
     "género aislado"),
    ("Caracterización del aislado",
     "virulencia, epidemiología y distribución geográfica del organismo identificado"),
]


def preguntar(etiqueta: str, defecto: str) -> str:
    respuesta = input(f"   {c(etiqueta, 'blanco')} {c(f'[{defecto}]', 'dim')} ").strip()
    return respuesta or defecto


def elegir_contexto() -> tuple[str, str]:
    """El contexto de investigación entra al prompt del modelo y cambia sus consultas.

    Es la parte más visible del carril generativo: mismo organismo medido, distinto
    encuadre, distintas preguntas a la literatura. Nadie programó esas frases.
    """
    print(c("   Contexto de la línea de investigación:", "gris"))
    for indice, (nombre, detalle) in enumerate(CONTEXTOS, start=1):
        print(f"     {c(str(indice), 'gen')}. {c(nombre, 'blanco')}")
        for linea in _envolver(detalle, ANCHO - 12):
            print(f"        {c(linea, 'dim')}")
    print()
    eleccion = preguntar("¿Cuál usamos?", "1")
    try:
        return CONTEXTOS[int(eleccion) - 1]
    except (ValueError, IndexError):
        return CONTEXTOS[0]


def elegir_secuenciacion() -> dict:
    """Contexto: sobre qué muestra del laboratorio se va a correr el experimento."""
    if MODO == "replay":
        return GRABACION["configuracion"]["secuenciacion"]

    from . import repo_seq
    secuenciaciones = repo_seq.list_secuenciaciones()
    listas = [s for s in secuenciaciones if any(
        a["formato"] == "fasta" and a["estadoValidacion"] == "valido" for a in s["archivos"]
    )]
    if not listas:
        error("No hay ninguna secuenciación con un FASTA válido cargado.")
        print(c("     Regístrala y sube el archivo desde la aplicación antes de correr la demo.", "gris"))
        raise SystemExit(1)

    print(c("   Secuenciaciones disponibles:", "gris"))
    for indice, s in enumerate(listas, start=1):
        print(f"     {c(str(indice), 'det')}. {c(s['codigo'], 'blanco')} "
              f"{c('· ' + s['origenEtiqueta'], 'gris')} "
              f"{c('· ' + (s['organismoDeclarado'] or 'sin organismo declarado'), 'dim')}")
    print()
    eleccion = preguntar("¿Cuál analizamos?", "1")
    try:
        return listas[int(eleccion) - 1]
    except (ValueError, IndexError):
        return listas[0]


def configurar() -> dict:
    print(c("  ── Configuración del experimento " + "─" * (ANCHO - 33), "dim"))
    print()
    secuenciacion = elegir_secuenciacion()
    if MODO == "replay":
        conf = dict(GRABACION["configuracion"])
        print(c("   Secuenciación", "gris") + "  " + c(secuenciacion["codigo"], "blanco"))
        print(c("   Contexto     ", "gris") + "  " + c(conf["contextoNombre"], "blanco"))
        print(c("   Temperatura  ", "gris") + "  " + c(str(conf["temperatura"]), "blanco"))
        print(c("   Semilla      ", "gris") + "  " + c(str(conf["seed"]), "blanco"))
        print()
        return conf

    print()
    nombre_contexto, detalle_contexto = elegir_contexto()
    print()
    temperatura = float(preguntar("Temperatura del modelo", "0.3"))
    seed = int(preguntar("Semilla (reproducibilidad)", "42"))
    control = preguntar("¿Generar también el control sin evidencia? (s/n)", "s").lower().startswith("s")
    return {
        "secuenciacion": secuenciacion,
        "contextoNombre": nombre_contexto,
        "contextoDetalle": detalle_contexto,
        "temperatura": temperatura,
        "seed": seed,
        "control": control,
    }


# --------------------------------------------------------------------------------------
# El flujo
# --------------------------------------------------------------------------------------

def correr() -> None:
    banner()
    conf = configurar()
    if MODO == "vivo":
        GRABACION["configuracion"] = conf
    secuenciacion = conf["secuenciacion"]
    control = conf.get("control", True)

    print()
    print(c("  ── Ejecutando " + "─" * (ANCHO - 14), "dim"))

    if MODO == "vivo":
        from . import repo_analisis, repo_fichas
        from .tools import ejecutar
        from .tools.base import ToolError
    else:
        repo_analisis = repo_fichas = ejecutar = None
        ToolError = Exception

    id_sec = secuenciacion["id"]
    fasta = [a for a in secuenciacion["archivos"]
             if a["formato"] == "fasta" and a["estadoValidacion"] == "valido"][-1]
    cronometro: dict[str, float] = {}

    # 1 — Validación y QC ---------------------------------------------------------------
    paso(1, "Validación del archivo y control de calidad", "det",
         "Comprobar un formato y contar bases son operaciones exactas. Un modelo de "
         "lenguaje devolvería un número plausible e inventado.")
    qc, cronometro["qc"] = etapa("qc", lambda: ejecutar("qc_secuencia", {"idArchivo": fasta["id"]}))
    metricas = qc["metricas"]
    log("det", f"qc_secuencia(idArchivo=…{fasta['id'][-6:]})",
        f"semaforo={qc['semaforo']}", cronometro["qc"])
    ok(f"{qc['formato'].upper()} válido · semáforo {c(qc['semaforo'], 'ok')}", cronometro["qc"])
    dato("Longitud", f"{metricas['longitudTotalPb']:,} pb")
    dato("Contenido GC", f"{metricas['gcPct']} %")
    dato("Bases ambiguas", f"{metricas['basesAmbiguas']} ({metricas['basesAmbiguasPct']} %)")

    # 2 — Lectura de la secuencia -------------------------------------------------------
    paso(2, "Extracción de la secuencia de consulta", "det",
         "Se toma la primera secuencia del FASTA y se recorta al máximo admitido por BLAST.")
    lectura, cronometro["lectura"] = etapa(
        "lectura", lambda: ejecutar("leer_secuencia", {"idArchivo": fasta["id"]}))
    log("det", "leer_secuencia(maxPb=20000)", f"{lectura['longitudPb']} pb", cronometro["lectura"])
    ok(f"{lectura['longitudPb']:,} pb listas para comparar", cronometro["lectura"])
    dato("Encabezado", lectura["encabezado"][:52])

    # 3 — BLAST -------------------------------------------------------------------------
    paso(3, "Comparación contra la base mundial de referencia", "ext",
         "BLAST del NCBI compara la secuencia contra genes 16S de cepas tipo. Es "
         "asíncrono: devuelve un identificador de trabajo y hay que consultarlo.")

    def _blast():
        envio = ejecutar("blast_enviar", {"secuencia": lectura["secuencia"], "baseDatos": "16S"})
        girador.actualizar(f"BLAST en curso · trabajo {envio['rid']}…")
        while True:
            time.sleep(10)
            estado = ejecutar("blast_resultado", {"rid": envio["rid"]})
            if estado["estado"] != "en_curso":
                return {"rid": envio["rid"], "baseDatos": envio["baseDatos"],
                        "estado": estado["estado"], "hits": estado.get("hits") or []}

    with Girador("enviando la consulta a NCBI…", "ext") as girador:
        blast, cronometro["blast"] = etapa("blast", _blast, minimo=6.0)

    hits = blast.get("hits") or []
    if not hits:
        error("BLAST no encontró coincidencias.")
        return
    if MODO == "vivo":
        repo_analisis.guardar_hits_blast(id_sec, blast["rid"], blast["baseDatos"], hits)
    log("ext", f"blast_enviar + blast_resultado(rid={blast['rid']})",
        f"{len(hits)} hits", cronometro["blast"])
    ok(f"{len(hits)} coincidencias · trabajo {blast['rid']}", cronometro["blast"])
    print()
    cabecera = "#".ljust(3) + "organismo".ljust(38) + "ident.".rjust(8) + "cobert.".rjust(9) + "  accession"
    print(f"     {c(cabecera, 'dim')}")
    for hit in hits[:5]:
        realce = "blanco" if hit["ranking"] == 1 else "gris"
        fila = (
            str(hit["ranking"]).ljust(3)
            + hit["organismo"][:36].ljust(38)
            + "{}%".format(hit["identidadPct"]).rjust(8)
            + "{}%".format(hit["coberturaPct"]).rjust(9)
        )
        print(f"     {c(fila, realce)}  {c(hit['accession'], 'dim')}")

    # 4 — Taxonomía ---------------------------------------------------------------------
    paso(4, "Resolución del linaje taxonómico", "ext",
         "El linaje se consulta con el identificador del hit medido. El modelo no "
         "interviene: si lo hiciera, estaría recordando en lugar de consultar.")
    taxid = hits[0].get("taxid") or hits[0].get("taxId")

    def _taxonomia():
        if not taxid:
            return None
        try:
            return ejecutar("resolver_taxonomia", {"taxid": taxid})
        except ToolError as e:
            error(str(e))
            return None

    taxonomia, cronometro["taxonomia"] = etapa("taxonomia", _taxonomia)
    if taxonomia:
        log("ext", f"resolver_taxonomia(taxid={taxid})",
            taxonomia["nombreCientifico"], cronometro["taxonomia"])
        ok(taxonomia["nombreCientifico"], cronometro["taxonomia"])
        dato("Linaje", " › ".join(n["nombre"] for n in taxonomia["linaje"][-4:]))

    # 5 — Consultas: PRIMER PASO GENERATIVO ---------------------------------------------
    paso(5, "Redacción de las consultas de búsqueda", "gen",
         "Aquí interviene el modelo por primera vez. Convierte el organismo medido y el "
         "contexto que elegimos en las preguntas correctas para la literatura. "
         "Es criterio, no plantilla.")
    print(f"     {c('contexto →', 'gen')} {c(conf['contextoDetalle'], 'gris')}")
    with Girador("el modelo está redactando las consultas…", "gen"):
        consultas, cronometro["consultas"] = etapa(
            "consultas",
            lambda: repo_fichas.generar_consultas(hits[0]["organismo"],
                                                  contexto=conf["contextoDetalle"]),
            minimo=1.2,
        )
    log("gen", "generar_consultas(temperature=0.4)",
        f"{len(consultas)} consultas", cronometro["consultas"])
    ok(f"{len(consultas)} consultas generadas", cronometro["consultas"])
    for consulta in consultas:
        print(f"     {c('›', 'gen')} {c(consulta, 'blanco')}")

    # 6 — PubMed ------------------------------------------------------------------------
    paso(6, "Recuperación de literatura científica", "ext",
         "Las consultas las escribió el modelo; ejecutarlas es determinista. Esa "
         "separación es el corazón del diseño: el modelo aporta criterio, no resultados.")

    def _pubmed():
        encontrados: list[dict] = []
        vistos: set[str] = set()
        for consulta in consultas:
            try:
                resultado = ejecutar("buscar_pubmed", {"consulta": consulta, "maxResultados": 5})
            except ToolError:
                continue
            for articulo in resultado["articulos"]:
                if articulo["pmid"] not in vistos:
                    vistos.add(articulo["pmid"])
                    encontrados.append(articulo)
            if len(encontrados) >= 5:
                break
        return encontrados[:5]

    with Girador("consultando PubMed…", "ext"):
        literatura, cronometro["pubmed"] = etapa("pubmed", _pubmed, minimo=2.0)
    log("ext", "buscar_pubmed(esearch + efetch)",
        f"{len(literatura)} artículos", cronometro["pubmed"])
    ok(f"{len(literatura)} artículos con resumen", cronometro["pubmed"])
    for articulo in literatura:
        print(f"     {c('PMID ' + articulo['pmid'], 'ext')} "
              f"{c('(' + (articulo['anio'] or '—') + ')', 'dim')} "
              f"{c(articulo['titulo'][:46], 'gris')}")

    # 7 — La ficha: SEGUNDO PASO GENERATIVO ---------------------------------------------
    paso(7, "Redacción de la ficha científica", "gen",
         "El modelo recibe todo lo medido y lo recuperado, y escribe la interpretación. "
         "No calcula nada ni decide la especie: interpreta hechos ajenos.")
    with Girador("el modelo está redactando la ficha…", "gen"):
        ficha, cronometro["ficha"] = etapa(
            "ficha",
            lambda: repo_fichas.generar_ficha(id_sec, {
                "temperatura": conf["temperatura"], "seed": conf["seed"],
                "etiquetaExperimento": f"demo · {conf['contextoNombre']}",
                "conEvidencia": True,
            }, "Demostración en terminal"),
            minimo=3.0,
        )
    log("gen", f"generar_ficha(t={conf['temperatura']}, seed={conf['seed']})",
        f"{ficha['tokensSalida']} tokens", cronometro["ficha"])
    ok(f"ficha generada con {ficha['modelo']}", cronometro["ficha"])
    dato("Tokens", f"{ficha['tokensEntrada']} entrada → {ficha['tokensSalida']} salida")
    dato("Temperatura / semilla", f"{ficha['temperatura']} / {ficha['seed']}")
    dato("Huella de evidencia", (ficha["evidenciaSha256"] or "")[:32] + "…")
    print()
    for linea in _envolver(ficha["secciones"].get("Resumen", "")[:430], ANCHO - 6):
        print(f"     {c(linea, 'blanco')}")

    # 8 — El control --------------------------------------------------------------------
    if control:
        paso(8, "Control del experimento: la misma pregunta sin evidencia", "gen",
             "Mismo modelo, misma temperatura, misma semilla. Lo único que cambia es "
             "que se retira la evidencia. Sirve para ver la alucinación de frente.")
        with Girador("el modelo responde a ciegas…", "gen"):
            ciega, cronometro["control"] = etapa(
                "control",
                lambda: repo_fichas.generar_ficha(id_sec, {
                    "temperatura": conf["temperatura"], "seed": conf["seed"],
                    "etiquetaExperimento": "demo-control", "conEvidencia": False,
                }, "Demostración en terminal"),
                minimo=3.0,
            )
        log("gen", "generar_ficha(conEvidencia=False)",
            "sin contexto de entrada", cronometro["control"])
        ok(f"generada sin evidencia · {ciega['tokensEntrada']} tokens de entrada",
           cronometro["control"])
        dato("Huella de evidencia", "ninguna — no se le entregó nada")
        print()
        for linea in _envolver(ciega["secciones"].get("Resumen", "")[:430], ANCHO - 6):
            print(f"     {c(linea, 'err')}")
        print()
        print(f"   {c('⚠', 'err')} {c('Compara ambos resúmenes: mismo modelo, misma configuración.', 'err')}")
        print(f"     {c('La diferencia es únicamente la evidencia.', 'err')}")

    # 9 — Cierre ------------------------------------------------------------------------
    paso(9, "Resumen de la corrida", "det",
         "Todo queda persistido: los hechos medidos, la evidencia con su fecha de "
         "consulta, y la ficha con los parámetros exactos que la produjeron.")
    total = sum(cronometro.values())
    deterministas = cronometro["qc"] + cronometro["lectura"]
    servicios = cronometro["blast"] + cronometro["taxonomia"] + cronometro["pubmed"]
    generativo = (cronometro.get("consultas", 0) + cronometro.get("ficha", 0)
                  + cronometro.get("control", 0))
    print()
    dato("Tiempo total", f"{total:.1f} s")
    dato("En pasos deterministas", f"{deterministas:.2f} s")
    dato("Esperando servicios", f"{servicios:.1f} s")
    dato("En el modelo", f"{generativo:.1f} s")
    print()
    print(f"     {c('●', 'det')} {c('deterministas', 'gris')}  "
          f"{c('4 pasos', 'blanco')}   {c(f'{100 * deterministas / total:4.1f} % del tiempo', 'dim')}")
    print(f"     {c('●', 'ext')} {c('servicios    ', 'gris')}  "
          f"{c('3 pasos', 'blanco')}   {c(f'{100 * servicios / total:4.1f} % del tiempo', 'dim')}")
    print(f"     {c('●', 'gen')} {c('generativos  ', 'gris')}  "
          f"{c(f'{3 if control else 2} pasos', 'blanco')}   "
          f"{c(f'{100 * generativo / total:4.1f} % del tiempo', 'dim')}")
    print()
    print(c("  " + "─" * ANCHO, "dim"))
    print(f"  {c('El modelo escribió las consultas y la interpretación.', 'gen')}")
    print(f"  {c('Ningún número de esta corrida lo produjo el modelo.', 'det')}")
    print(c("  " + "─" * ANCHO, "dim"))
    print()


# --------------------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------------------

def _guardar_grabacion(ruta: str, conf: dict) -> None:
    salida = {
        "grabadaEn": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "configuracion": conf,
        "pasos": REGISTRO,
    }
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1, default=str)
    print(f"   {c('▸', 'det')} {c('corrida guardada en ' + ruta, 'gris')}")
    print(f"     {c('reprodúcela con:  py -3 -m app.demo --replay ' + ruta, 'dim')}")
    print()


def _cerrar_pool() -> None:
    """Cierra el pool antes de salir.

    Sin esto el intérprete termina con el hilo del pool vivo y psycopg imprime un
    PythonFinalizationError: ruido feo justo en el cierre de una demostración.
    """
    try:
        from .db import close_pool
        close_pool()
    except Exception:
        pass


def main() -> None:
    global MODO, ESCALA, GRABACION, LOGS

    parser = argparse.ArgumentParser(description="Demostración del pipeline de FagoLab.")
    parser.add_argument("--replay", metavar="ARCHIVO",
                        help="reproduce una corrida grabada en lugar de ejecutarla")
    parser.add_argument("--grabar", metavar="ARCHIVO",
                        help="ejecuta en vivo y guarda todo lo ocurrido")
    parser.add_argument("--rapido", action="store_true",
                        help="en reproducción, acelera las esperas 4x")
    parser.add_argument("--sin-logs", action="store_true",
                        help="oculta la traza por invocación")
    args = parser.parse_args()

    _activar_ansi()
    LOGS = not args.sin_logs
    if args.rapido:
        ESCALA = 4.0
    if args.replay:
        MODO = "replay"
        with open(args.replay, encoding="utf-8") as f:
            GRABACION = json.load(f)

    try:
        correr()
        if args.grabar and MODO == "vivo":
            _guardar_grabacion(args.grabar, GRABACION.get("configuracion", {}))
        if MODO == "vivo":
            _cerrar_pool()
    except KeyboardInterrupt:
        print("\n\n" + c("  Demostración interrumpida.", "gris") + "\n")
    except Exception as e:  # noqa: BLE001 — en una demo, el error se muestra legible
        print()
        error(f"{type(e).__name__}: {e}")
        print()


if __name__ == "__main__":
    main()
