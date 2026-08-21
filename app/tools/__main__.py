"""Puerta de línea de comandos del catálogo de herramientas.

Es la misma implementación que usa la API; solo cambia cómo se invoca. Existe para que
un orquestador externo (n8n, un agente en la nube, un nodo de cómputo con GPU) pueda
ejecutar una herramienta sin hablar HTTP con FagoLab.

    py -3 -m app.tools --catalogo
    py -3 -m app.tools qc_secuencia --idArchivo 3f2c...
    py -3 -m app.tools blast_enviar --json '{"secuencia": "ACGT..."}'

Salida siempre JSON por stdout. Los errores esperados salen por stderr con código 1.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import catalogo, ejecutar
from .base import ToolError


def _parsear_argumentos(sobrantes: list[str]) -> dict:
    """Convierte --clave valor en un dict. Los pares sueltos se ignoran en silencio."""
    entrada: dict[str, str] = {}
    i = 0
    while i < len(sobrantes):
        actual = sobrantes[i]
        if actual.startswith("--") and i + 1 < len(sobrantes):
            entrada[actual[2:]] = sobrantes[i + 1]
            i += 2
        else:
            i += 1
    return entrada


def main() -> int:
    # La salida es un contrato: JSON en UTF-8, pase lo que pase. Sin esto, en una consola
    # de Windows (cp1252) un abstract de PubMed con un espacio fino tumba la herramienta.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="app.tools", description="Herramientas de FagoLab.")
    parser.add_argument("herramienta", nargs="?", help="Nombre de la herramienta a ejecutar.")
    parser.add_argument("--json", dest="payload", help="Parámetros como objeto JSON.")
    parser.add_argument("--catalogo", action="store_true", help="Imprime el catálogo y termina.")
    args, sobrantes = parser.parse_known_args()

    if args.catalogo or not args.herramienta:
        print(json.dumps(catalogo(), ensure_ascii=False, indent=2))
        return 0

    if args.payload:
        try:
            entrada = json.loads(args.payload)
        except json.JSONDecodeError as e:
            print(f"El parámetro --json no es JSON válido: {e}", file=sys.stderr)
            return 1
    else:
        entrada = _parsear_argumentos(sobrantes)

    try:
        resultado = ejecutar(args.herramienta, entrada)
    except ToolError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
