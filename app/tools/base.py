"""Contrato de una herramienta de FagoLab.

Una herramienta es una función con firma documentada: nombre, parámetros con esquema
JSON, salida JSON y efecto declarado. **Una sola implementación, varias puertas**:

    - la API la expone en /api/tools
    - el agente la llama dentro de su bucle
    - la línea de comandos la ejecuta:  py -3 -m app.tools qc_secuencia --json '{...}'
    - n8n o cualquier orquestador externo la invocan por HTTP

Por eso el handler es una función *síncrona* que recibe un dict y devuelve un dict
serializable: es el mínimo común denominador que entienden las cuatro puertas.

Cada herramienta declara además su **plano de ejecución**, que no es documentación
decorativa: dice si puede correr dentro del request (`proceso`), si tarda y debe irse a
una corrida en segundo plano (`job`), o si necesita hardware que este servidor no tiene
(`gpu`). El orquestador lo lee para decidir cómo invocarla.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[[dict], dict]

# Dónde puede correr una herramienta.
PLANOS = ("proceso", "job", "gpu")


class ToolError(Exception):
    """Fallo esperado de una herramienta (entrada inválida, servicio externo caído).

    Se distingue de un error de programación: este se le puede mostrar a la usuaria.
    """


@dataclass(frozen=True)
class Tool:
    nombre: str
    descripcion: str
    parametros: dict          # JSON Schema del objeto de entrada
    devuelve: str             # descripción corta de la salida
    permiso: str              # clave del catálogo ACL; "" = solo autenticación
    plano: str                # proceso | job | gpu
    red: bool                 # ¿sale a internet?
    handler: Handler = field(repr=False)

    def firma(self) -> dict:
        """Contrato público. Es lo que ve el agente y lo que documenta el catálogo."""
        return {
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "parametros": self.parametros,
            "devuelve": self.devuelve,
            "permiso": self.permiso,
            "plano": self.plano,
            "red": self.red,
        }

    def openai(self) -> dict:
        """Misma herramienta en el formato de function calling que espera el proveedor."""
        return {
            "type": "function",
            "function": {
                "name": self.nombre,
                "description": self.descripcion,
                "parameters": self.parametros,
            },
        }


TOOLS: dict[str, Tool] = {}


def registrar(
    nombre: str,
    descripcion: str,
    *,
    parametros: dict,
    devuelve: str,
    permiso: str = "",
    plano: str = "proceso",
    red: bool = False,
) -> Callable[[Handler], Handler]:
    """Decorador que publica una función como herramienta del catálogo."""
    if plano not in PLANOS:
        raise ValueError(f"Plano de ejecución inválido: {plano}")

    def envoltura(handler: Handler) -> Handler:
        if nombre in TOOLS:
            raise RuntimeError(f"Herramienta duplicada: {nombre}")
        TOOLS[nombre] = Tool(
            nombre=nombre,
            descripcion=descripcion,
            parametros=parametros,
            devuelve=devuelve,
            permiso=permiso,
            plano=plano,
            red=red,
            handler=handler,
        )
        return handler

    return envoltura


def obtener(nombre: str) -> Tool:
    tool = TOOLS.get(nombre)
    if tool is None:
        raise ToolError(f"No existe la herramienta '{nombre}'.")
    return tool


def catalogo() -> list[dict]:
    return [TOOLS[n].firma() for n in sorted(TOOLS)]


def _validar(tool: Tool, entrada: dict) -> dict:
    """Validación mínima contra el esquema declarado.

    No es un validador de JSON Schema completo a propósito: solo comprueba lo que de
    verdad rompe una herramienta — campos requeridos ausentes y tipos evidentes. Un
    validador completo añadiría una dependencia para ganar muy poco aquí.
    """
    esquema = tool.parametros or {}
    propiedades = esquema.get("properties") or {}
    faltantes = [c for c in (esquema.get("required") or []) if entrada.get(c) in (None, "")]
    if faltantes:
        raise ToolError(f"Faltan parámetros obligatorios: {', '.join(faltantes)}.")

    tipos = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list}
    limpio: dict[str, Any] = {}
    for clave, valor in entrada.items():
        if clave not in propiedades:
            continue  # se ignora lo que no está en el contrato
        esperado = tipos.get((propiedades[clave] or {}).get("type", "string"))
        if valor is not None and esperado and not isinstance(valor, esperado):
            # Los números que llegan como texto desde la CLI o un formulario se convierten.
            try:
                valor = (esperado[0] if isinstance(esperado, tuple) else esperado)(valor)
            except (TypeError, ValueError):
                raise ToolError(f"El parámetro '{clave}' debe ser de tipo {propiedades[clave]['type']}.")
        limpio[clave] = valor
    return limpio


def ejecutar(nombre: str, entrada: dict | None = None) -> dict:
    """Punto de entrada único. Todas las puertas terminan aquí."""
    tool = obtener(nombre)
    return tool.handler(_validar(tool, entrada or {}))
