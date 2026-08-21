"""Catálogo de herramientas de FagoLab.

Importar este paquete registra todas las herramientas disponibles. El catálogo es el
activo estable del sistema: la API, el agente, la línea de comandos y cualquier
orquestador externo llaman exactamente el mismo contrato.
"""
from .base import Tool, ToolError, catalogo, ejecutar, obtener, registrar, TOOLS

# El import tiene efecto: cada módulo registra sus herramientas al cargarse.
from . import filogenia, laboratorio, ncbi, pubmed, secuencia  # noqa: F401  (registro por importación)

__all__ = ["Tool", "ToolError", "catalogo", "ejecutar", "obtener", "registrar", "TOOLS"]
