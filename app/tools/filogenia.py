"""Árbol filogenético orientativo entre la secuencia analizada y sus hits de BLAST.

Honestidad primero: **esto no es una filogenia publicable**. Un árbol serio exige
alineamiento múltiple curado, un modelo evolutivo elegido con criterio y soporte por
bootstrap. Aquí se calcula una distancia *alineamiento-libre* por perfiles de k-meros
—la misma familia de métodos que usan herramientas como Mash— y se agrupa por
neighbor-joining. Sirve para ver de un vistazo con qué se agrupa la muestra y qué
queda lejos; no sirve para afirmar relaciones evolutivas.

Se hace así a propósito: un alineamiento par a par de 1.5 kb en Python puro tardaría
minutos, y añadir una dependencia pesada para un resultado que igual habría que
etiquetar como orientativo no compensa. La limitación se declara en la salida y termina
citada en la ficha.
"""
from __future__ import annotations

import math

from .base import ToolError, registrar
from .ncbi import descargar_ncbi

# Tamaño de k-mero. Con k=8 hay 65 536 combinaciones posibles: suficiente resolución para
# distinguir 16S de especies cercanas sin que el perfil se vuelva ruido disperso.
K = 8

MAX_REFERENCIAS = 8


def _perfil(secuencia: str, k: int = K) -> dict[str, int]:
    perfil: dict[str, int] = {}
    secuencia = secuencia.upper()
    for i in range(len(secuencia) - k + 1):
        kmero = secuencia[i:i + k]
        if "N" in kmero:
            continue
        perfil[kmero] = perfil.get(kmero, 0) + 1
    return perfil


def _distancia(a: dict[str, int], b: dict[str, int]) -> float:
    """Distancia coseno entre perfiles de k-meros: 0 = idénticos, 1 = sin nada en común."""
    comunes = set(a) & set(b)
    if not comunes:
        return 1.0
    producto = sum(a[k] * b[k] for k in comunes)
    norma_a = math.sqrt(sum(v * v for v in a.values()))
    norma_b = math.sqrt(sum(v * v for v in b.values()))
    if not norma_a or not norma_b:
        return 1.0
    return max(0.0, 1.0 - producto / (norma_a * norma_b))


def _neighbor_joining(nombres: list[str], matriz: list[list[float]]) -> dict:
    """Neighbor-joining clásico (Saitou & Nei). Devuelve el árbol como nodos anidados."""
    n = len(nombres)
    if n < 3:
        raise ToolError("Se necesitan al menos tres secuencias para construir un árbol.")

    # Cada agrupación viva: su etiqueta y el subárbol que representa.
    nodos: list[dict | None] = [{"nombre": nombres[i], "hoja": True, "hijos": []} for i in range(n)]
    d = [fila[:] for fila in matriz]
    vivos = list(range(n))

    while len(vivos) > 2:
        m = len(vivos)
        # Q(i,j) = (m-2)·d(i,j) − suma(d(i,·)) − suma(d(j,·))
        sumas = {i: sum(d[i][j] for j in vivos if j != i) for i in vivos}
        mejor = None
        mejor_q = float("inf")
        for indice_i, i in enumerate(vivos):
            for j in vivos[indice_i + 1:]:
                q = (m - 2) * d[i][j] - sumas[i] - sumas[j]
                if q < mejor_q:
                    mejor_q, mejor = q, (i, j)

        i, j = mejor
        dij = d[i][j]
        rama_i = 0.5 * dij + (sumas[i] - sumas[j]) / (2 * (m - 2)) if m > 2 else 0.5 * dij
        rama_j = dij - rama_i
        nuevo = {
            "nombre": "",
            "hoja": False,
            "hijos": [
                {**nodos[i], "rama": round(max(rama_i, 0.0), 6)},
                {**nodos[j], "rama": round(max(rama_j, 0.0), 6)},
            ],
        }

        # El nuevo nodo ocupa la posición de i; j desaparece.
        for k in vivos:
            if k in (i, j):
                continue
            d[i][k] = d[k][i] = max(0.0, 0.5 * (d[i][k] + d[j][k] - dij))
        nodos[i] = nuevo
        nodos[j] = None
        vivos.remove(j)

    a, b = vivos
    return {
        "nombre": "",
        "hoja": False,
        "hijos": [
            {**nodos[a], "rama": round(d[a][b] / 2, 6)},
            {**nodos[b], "rama": round(d[a][b] / 2, 6)},
        ],
    }


def _newick(nodo: dict) -> str:
    if nodo.get("hoja"):
        etiqueta = nodo["nombre"].replace(" ", "_").replace(",", "").replace(":", "")
        return f"{etiqueta}:{nodo.get('rama', 0)}"
    partes = ",".join(_newick(h) for h in nodo["hijos"])
    rama = nodo.get("rama")
    return f"({partes})" + (f":{rama}" if rama is not None else "")


@registrar(
    "construir_arbol",
    "Construye un árbol filogenético orientativo entre una secuencia y las secuencias de "
    "referencia de sus hits de BLAST, usando distancias por k-meros y neighbor-joining. "
    "No sustituye una filogenia con alineamiento múltiple y bootstrap.",
    parametros={
        "type": "object",
        "properties": {
            "consulta": {"type": "string", "description": "Secuencia de nucleótidos a ubicar."},
            "etiquetaConsulta": {"type": "string", "description": "Nombre para la hoja de la consulta."},
            "accessions": {
                "type": "array",
                "description": "Accessions de NCBI de las secuencias de referencia (2 a 8).",
            },
        },
        "required": ["consulta", "accessions"],
    },
    devuelve="Objeto con el árbol anidado, su Newick, la matriz de distancias y la limitación declarada.",
    permiso="analisis.corridas.create",
    plano="job",
    red=True,
)
def construir_arbol(entrada: dict) -> dict:
    accessions = [str(a).strip() for a in (entrada.get("accessions") or []) if str(a).strip()]
    accessions = list(dict.fromkeys(accessions))[:MAX_REFERENCIAS]
    if len(accessions) < 2:
        raise ToolError("Se necesitan al menos dos secuencias de referencia.")

    etiqueta = (entrada.get("etiquetaConsulta") or "Consulta").strip()
    secuencias: list[tuple[str, str]] = [(etiqueta, entrada["consulta"])]
    referencias: list[dict] = []

    for accession in accessions:
        try:
            bajada = descargar_ncbi({"accession": accession})
        except ToolError:
            continue  # una referencia caída no debe tumbar el árbol entero
        organismo = " ".join(bajada["encabezado"].split()[1:3]) or accession
        secuencias.append((f"{organismo} ({accession})", bajada["secuencia"]))
        referencias.append({
            "accession": accession,
            "organismo": organismo,
            "longitudPb": bajada["longitudPb"],
        })

    if len(secuencias) < 3:
        raise ToolError("No se pudieron descargar suficientes secuencias de referencia de NCBI.")

    nombres = [n for n, _ in secuencias]
    perfiles = [_perfil(s) for _, s in secuencias]
    matriz = [
        [0.0 if i == j else _distancia(perfiles[i], perfiles[j]) for j in range(len(perfiles))]
        for i in range(len(perfiles))
    ]

    arbol = _neighbor_joining(nombres, matriz)
    return {
        "etiquetaConsulta": etiqueta,
        "referencias": referencias,
        "metodo": f"Distancia coseno entre perfiles de k-meros (k={K}) + neighbor-joining",
        "arbol": arbol,
        "newick": _newick(arbol) + ";",
        "matriz": {
            "nombres": nombres,
            "valores": [[round(v, 5) for v in fila] for fila in matriz],
        },
        "limitacion": (
            "Árbol orientativo. Se calcula sin alineamiento múltiple, sin modelo evolutivo "
            "y sin soporte por bootstrap; muestra agrupamiento por similitud de composición, "
            "no relaciones evolutivas confirmadas."
        ),
    }
