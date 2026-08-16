"""Catálogo canónico de permisos y reglas ACL.

La política es fail-closed: cualquier endpoint /api nuevo que no aparezca aquí queda
bloqueado. La tabla permisos_acceso materializa este catálogo para que el panel de
administración pueda editar asignaciones y mostrar qué vista/acción/endpoint controla.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class PermissionDef:
    key: str
    module: str
    resource: str
    action: str
    description: str
    kind: str = "accion"
    frontend_route: str | None = None
    risk: str = "normal"


@dataclass(frozen=True)
class AclRule:
    method: str
    pattern: str
    permission: str | None

    def matches(self, method: str, path: str) -> bool:
        return (self.method == "*" or self.method == method.upper()) and bool(
            re.fullmatch(self.pattern, path)
        )


def permission(
    key: str,
    description: str,
    *,
    kind: str = "accion",
    route: str | None = None,
    risk: str = "normal",
) -> PermissionDef:
    module, resource, action = key.split(".", 2)
    return PermissionDef(key, module, resource, action, description, kind, route, risk)


PERMISSIONS: tuple[PermissionDef, ...] = (
    permission("dashboard.main.view", "Ver el resumen general.", kind="vista", route="/"),
    permission("security.users.view", "Consultar usuarios.", risk="alto"),
    permission("security.users.create", "Crear cuentas de usuario.", risk="alto"),
    permission("security.users.update", "Editar o suspender cuentas.", risk="alto"),
    permission("security.users.approve", "Aprobar solicitudes de acceso.", risk="alto"),
    permission("security.users.password.reset", "Reiniciar contrasenas de otras personas.", risk="critico"),
    permission("security.users.access.manage", "Asignar roles, grupos y excepciones a usuarios.", risk="critico"),
    permission("security.roles.view", "Consultar roles."),
    permission("security.roles.create", "Crear roles.", risk="alto"),
    permission("security.roles.update", "Editar o desactivar roles.", risk="alto"),
    permission("security.roles.permissions.manage", "Editar la matriz de permisos de roles.", risk="critico"),
    permission("security.groups.view", "Consultar grupos."),
    permission("security.groups.create", "Crear grupos.", risk="alto"),
    permission("security.groups.update", "Editar o desactivar grupos.", risk="alto"),
    permission("security.groups.access.manage", "Editar miembros, roles y excepciones de grupos.", risk="critico"),
    permission("security.permissions.view", "Consultar el catalogo canonico de permisos.", risk="alto"),
    permission("security.sessions.view", "Consultar sesiones abiertas.", risk="alto"),
    permission("security.sessions.revoke", "Revocar sesiones de usuarios.", risk="critico"),
    permission("security.superadmins.manage", "Promover o retirar el nivel de superadministrador.", risk="critico"),
    permission("security.presence.view", "Consultar quién está conectado en tiempo real.", risk="alto"),
    permission("proyecto.info.view", "Ver la información del proyecto.", kind="vista", route="/proyecto"),
    permission("search.global.use", "Usar el buscador global."),
    permission("recepciones.records.view", "Consultar recepciones.", kind="vista", route="/recepciones"),
    permission("recepciones.records.create", "Crear recepciones."),
    permission("recepciones.records.update", "Editar recepciones."),
    permission("aislamiento.workflow.view", "Abrir el flujo de aislamiento.", kind="vista", route="/aislamiento"),
    permission("aislamiento.workflow.create", "Registrar peces, muestras y cajas."),
    permission("aislamiento.catalogs.manage", "Crear órganos y medios de cultivo."),
    permission("peces.records.view", "Consultar peces.", kind="vista", route="/peces"),
    permission("cajas.records.view", "Consultar cajas Petri.", kind="vista", route="/cajas"),
    permission("cajas.records.update", "Editar estado, trazabilidad y observaciones de cajas."),
    permission("cajas.records.delete", "Eliminar cajas Petri.", risk="alto"),
    permission("cajas.photos.manage", "Subir o eliminar fotografías de cajas."),
    permission("subcultivos.records.view", "Consultar subcultivos.", kind="vista", route="/subcultivos"),
    permission("subcultivos.records.create", "Crear subcultivos."),
    permission("subcultivos.records.update", "Editar subcultivos y pureza."),
    permission("subcultivos.records.delete", "Eliminar subcultivos.", risk="alto"),
    permission("subcultivos.dna.extract", "Registrar extracción de ADN."),
    permission("nanodrop.readings.view", "Consultar lecturas NanoDrop.", kind="vista", route="/nanodrop"),
    permission("nanodrop.readings.create", "Registrar lecturas NanoDrop."),
    permission("nanodrop.readings.update", "Editar lecturas y decisiones NanoDrop."),
    permission("pcr.runs.view", "Consultar PCR y corridas.", kind="vista", route="/pcr"),
    permission("pcr.runs.create", "Crear PCR y corridas."),
    permission("pcr.controls.manage", "Administrar controles positivos."),
    permission("electroforesis.gels.view", "Consultar geles.", kind="vista", route="/electroforesis"),
    permission("electroforesis.gels.create", "Registrar geles y resultados."),
    permission("protocolos.content.view", "Consultar protocolos.", kind="vista", route="/protocolos"),
    permission("biblioteca.documents.view", "Consultar biblioteca.", kind="vista", route="/biblioteca"),
    permission("biblioteca.documents.create", "Agregar referencias y PDFs."),
    permission("biblioteca.documents.update", "Editar referencias."),
    permission("biblioteca.documents.delete", "Eliminar referencias.", risk="alto"),
    permission("modelos.content.view", "Consultar modelos de IA.", kind="vista", route="/modelos-ia"),
    permission("reportes.analytics.view", "Consultar reportes.", kind="vista", route="/reportes"),
    permission("reportes.files.export", "Exportar Excel y PDF."),
    permission("etiquetas.labels.view", "Consultar e imprimir etiquetas.", kind="vista", route="/etiquetas"),
    permission("media.files.view", "Consultar fotografías y archivos."),
    permission("media.files.create", "Subir fotografías y archivos."),
    permission("media.files.delete", "Eliminar fotografías y archivos.", risk="alto"),
    permission("datos.database.view", "Ver el panel de datos.", kind="vista", route="/modelo", risk="alto"),
    permission("datos.backups.manage", "Crear y descargar respaldos.", risk="alto"),
    permission("datos.database.restore", "Restaurar respaldos.", risk="critico"),
    permission("datos.database.seed", "Restaurar o guardar datos semilla.", risk="critico"),
    permission("datos.database.delete_all", "Vaciar los datos experimentales conservando seguridad.", risk="critico"),
    permission("security.panel.view", "Abrir el panel de seguridad.", kind="vista", route="/seguridad"),
    permission("security.audit.view", "Consultar la bitácora de auditoría.", risk="alto"),
    permission("chat.panel.view", "Abrir el panel de mensajes.", kind="vista", route="/mensajes"),
    permission("chat.conversations.view", "Consultar conversaciones y su historial."),
    permission("chat.conversations.create", "Crear conversaciones directas."),
    permission("chat.groups.create", "Crear conversaciones grupales."),
    permission("chat.groups.manage", "Administrar integrantes y datos de grupos.", risk="alto"),
    permission("chat.messages.send", "Enviar mensajes en conversaciones."),
    permission("chat.messages.moderate", "Editar o borrar mensajes de otras personas.", risk="alto"),
    permission("tareas.panel.view", "Abrir el panel de tareas.", kind="vista", route="/tareas"),
    permission("tareas.items.view", "Consultar tareas y sus detalles."),
    permission("tareas.items.create", "Crear tareas."),
    permission("tareas.items.update", "Editar tareas."),
    permission("tareas.items.transition", "Cambiar una tarea de estado."),
    permission("tareas.items.assign", "Asignar responsables de tareas."),
    permission("tareas.items.delete", "Eliminar tareas.", risk="alto"),
    permission("tareas.comments.create", "Comentar tareas."),
    permission("tareas.comments.moderate", "Editar o borrar comentarios de otras personas.", risk="alto"),
    permission("tareas.attachments.manage", "Adjuntar o retirar archivos de tareas."),
    permission("tareas.activity.view", "Consultar el historial de actividad de tareas."),
    permission("tareas.spaces.manage", "Administrar espacios de trabajo.", risk="alto"),
    permission("tareas.config.view", "Consultar la configuración de tareas.", risk="alto"),
    permission("tareas.fields.manage", "Administrar campos personalizados.", risk="alto"),
    permission("tareas.workflow.manage", "Administrar flujos y transiciones.", risk="critico"),
    permission("tareas.permissions.manage", "Administrar esquemas de permisos de tareas.", risk="critico"),
    permission("ia.panel.view", "Abrir el asistente.", kind="vista", route="/asistente"),
    permission("ia.agent.ask", "Consultar al asistente en modo lectura."),
    permission("ia.agent.act", "Pedir acciones al asistente con confirmación.", risk="alto"),
    permission("ia.conversations.manage", "Crear y administrar conversaciones del asistente."),
    permission("ia.shell.execute", "Ejecutar comandos locales mediante el asistente.", risk="critico"),
    permission("ia.config.view", "Consultar la configuración global del asistente.", risk="alto"),
    permission("ia.config.manage", "Cambiar la configuración global y la llave del asistente.", risk="critico"),
    permission("ia.connectors.manage", "Administrar conectores externos del asistente.", risk="alto"),
    permission("ia.policy.manage", "Administrar las políticas del asistente.", risk="critico"),
    permission("ia.usage.view", "Consultar consumo y costos del asistente.", risk="alto"),
)


def rule(method: str, pattern: str, permission_key: str | None) -> AclRule:
    return AclRule(method.upper(), pattern, permission_key)


# permission=None significa “usuario autenticado”, no ruta pública.
ACL_RULES: tuple[AclRule, ...] = (
    rule("GET", r"/api/auth/me", None),
    rule("GET", r"/api/auth/directory", None),
    rule("PATCH", r"/api/auth/profile", None),
    rule("POST", r"/api/auth/profile/(avatar|portada)", None),
    rule("DELETE", r"/api/auth/profile/(avatar|portada)", None),
    rule("POST", r"/api/auth/logout", None),
    rule("POST", r"/api/auth/change-password", None),
    rule("POST", r"/api/auth/page-view", None),
    rule("GET", r"/api/auth/sessions", None),
    rule("DELETE", r"/api/auth/sessions/[0-9a-fA-F-]+", None),
    rule("GET", r"/api/auth/admin/overview", "security.panel.view"),
    rule("GET", r"/api/auth/admin/users", "security.users.view"),
    rule("POST", r"/api/auth/admin/users", "security.users.create"),
    rule("PATCH", r"/api/auth/admin/users/[0-9a-fA-F-]+", "security.users.update"),
    rule("POST", r"/api/auth/admin/users/[0-9a-fA-F-]+/approve", "security.users.approve"),
    rule("POST", r"/api/auth/admin/users/[0-9a-fA-F-]+/reset-password", "security.users.password.reset"),
    rule("PUT", r"/api/auth/admin/users/[0-9a-fA-F-]+/access", "security.users.access.manage"),
    rule("PUT", r"/api/auth/admin/users/[0-9a-fA-F-]+/superadmin", "security.superadmins.manage"),
    rule("GET", r"/api/auth/admin/roles", "security.roles.view"),
    rule("POST", r"/api/auth/admin/roles", "security.roles.create"),
    rule("PATCH", r"/api/auth/admin/roles/[0-9a-fA-F-]+", "security.roles.update"),
    rule("PUT", r"/api/auth/admin/roles/[0-9a-fA-F-]+/permissions", "security.roles.permissions.manage"),
    rule("GET", r"/api/auth/admin/groups", "security.groups.view"),
    rule("POST", r"/api/auth/admin/groups", "security.groups.create"),
    rule("PATCH", r"/api/auth/admin/groups/[0-9a-fA-F-]+", "security.groups.update"),
    rule("PUT", r"/api/auth/admin/groups/[0-9a-fA-F-]+/access", "security.groups.access.manage"),
    rule("GET", r"/api/auth/admin/permissions", "security.permissions.view"),
    rule("GET", r"/api/auth/admin/audit", "security.audit.view"),
    rule("GET", r"/api/auth/admin/sessions", "security.sessions.view"),
    rule("DELETE", r"/api/auth/admin/sessions/[0-9a-fA-F-]+", "security.sessions.revoke"),
    rule("GET", r"/api/proyecto", "proyecto.info.view"),
    rule("GET", r"/api/dashboard", "dashboard.main.view"),
    rule("GET", r"/api/catalogos/(organos|medios|preferencias-siembra)", "aislamiento.workflow.view"),
    rule("POST", r"/api/catalogos/(organos|medios)", "aislamiento.catalogs.manage"),
    rule("GET", r"/api/recepciones", "recepciones.records.view"),
    rule("POST", r"/api/recepciones", "recepciones.records.create"),
    rule("PATCH", r"/api/recepciones/[0-9a-fA-F-]+", "recepciones.records.update"),
    rule("POST", r"/api/aislamiento", "aislamiento.workflow.create"),
    rule("GET", r"/api/peces", "peces.records.view"),
    rule("GET", r"/api/muestras", "peces.records.view"),
    rule("GET", r"/api/cajas", "cajas.records.view"),
    rule("PATCH", r"/api/cajas/[0-9a-fA-F-]+/trazabilidad", "cajas.records.update"),
    rule("POST", r"/api/cajas/[0-9a-fA-F-]+/observacion", "cajas.records.update"),
    rule("DELETE", r"/api/cajas/[0-9a-fA-F-]+", "cajas.records.delete"),
    rule("GET", r"/api/subcultivos", "subcultivos.records.view"),
    rule("PATCH", r"/api/subcultivos/[0-9a-fA-F-]+", "subcultivos.records.update"),
    rule("POST", r"/api/cajas/[0-9a-fA-F-]+/subcultivo(s)?", "subcultivos.records.create"),
    rule("DELETE", r"/api/subcultivos/[0-9a-fA-F-]+", "subcultivos.records.delete"),
    rule("POST", r"/api/subcultivos/[0-9a-fA-F-]+/extraccion", "subcultivos.dna.extract"),
    rule("GET", r"/api/extracciones", "subcultivos.records.view"),
    rule("GET", r"/api/viales", "nanodrop.readings.view"),
    rule("GET", r"/api/nanodrop", "nanodrop.readings.view"),
    rule("POST", r"/api/viales/[0-9a-fA-F-]+/nanodrop", "nanodrop.readings.create"),
    rule("PATCH", r"/api/nanodrop/[0-9a-fA-F-]+", "nanodrop.readings.update"),
    rule("GET", r"/api/pcr", "pcr.runs.view"),
    rule("GET", r"/api/pcr/pendientes", "pcr.runs.view"),
    rule("GET", r"/api/pcr/corridas", "pcr.runs.view"),
    rule("GET", r"/api/pcr/corridas/[0-9a-fA-F-]+/pozos", "pcr.runs.view"),
    rule("POST", r"/api/viales/[0-9a-fA-F-]+/pcr", "pcr.runs.create"),
    rule("POST", r"/api/pcr/corridas", "pcr.runs.create"),
    rule("GET", r"/api/positivos", "pcr.runs.view"),
    rule("POST", r"/api/positivos", "pcr.controls.manage"),
    rule("PATCH", r"/api/positivos/[0-9a-fA-F-]+", "pcr.controls.manage"),
    rule("GET", r"/api/geles", "electroforesis.gels.view"),
    rule("POST", r"/api/geles", "electroforesis.gels.create"),
    rule("GET", r"/api/etiquetas", "etiquetas.labels.view"),
    rule("GET", r"/api/reportes/resumen", "reportes.analytics.view"),
    rule("GET", r"/api/reportes/(excel|pdf)", "reportes.files.export"),
    rule("GET", r"/api/media/objeto/[^/]+", "media.files.view"),
    rule("POST", r"/api/media", "media.files.create"),
    rule("DELETE", r"/api/media/[0-9a-fA-F-]+", "media.files.delete"),
    rule("GET", r"/api/biblioteca", "biblioteca.documents.view"),
    rule("POST", r"/api/biblioteca", "biblioteca.documents.create"),
    rule("PATCH", r"/api/biblioteca/[0-9a-fA-F-]+", "biblioteca.documents.update"),
    rule("DELETE", r"/api/biblioteca/[0-9a-fA-F-]+", "biblioteca.documents.delete"),
    rule("GET", r"/api/admin/estado", "datos.database.view"),
    rule("GET", r"/api/admin/exportar", "datos.backups.manage"),
    rule("GET", r"/api/admin/respaldos", "datos.backups.manage"),
    rule("POST", r"/api/admin/respaldos", "datos.backups.manage"),
    rule("POST", r"/api/admin/respaldos/restaurar", "datos.database.restore"),
    rule("POST", r"/api/admin/importar", "datos.database.restore"),
    rule("POST", r"/api/admin/sembrar", "datos.database.seed"),
    rule("POST", r"/api/admin/guardar-semilla", "datos.database.seed"),
    rule("POST", r"/api/admin/limpiar", "datos.database.delete_all"),
    # Chat: las rutas de grupo preceden las paramétricas.
    rule("GET", r"/api/chat/conversaciones", "chat.conversations.view"),
    rule("POST", r"/api/chat/conversaciones", "chat.conversations.create"),
    rule("POST", r"/api/chat/grupos", "chat.groups.create"),
    rule("GET", r"/api/chat/conversaciones/[0-9a-fA-F-]+", "chat.conversations.view"),
    rule("PATCH", r"/api/chat/conversaciones/[0-9a-fA-F-]+", "chat.groups.manage"),
    rule("DELETE", r"/api/chat/conversaciones/[0-9a-fA-F-]+", "chat.groups.manage"),
    rule("PUT", r"/api/chat/conversaciones/[0-9a-fA-F-]+/miembros", "chat.groups.manage"),
    rule("GET", r"/api/chat/conversaciones/[0-9a-fA-F-]+/mensajes", "chat.conversations.view"),
    rule("POST", r"/api/chat/conversaciones/[0-9a-fA-F-]+/mensajes", "chat.messages.send"),
    rule("PATCH", r"/api/chat/mensajes/[0-9a-fA-F-]+", "chat.messages.send"),
    rule("DELETE", r"/api/chat/mensajes/[0-9a-fA-F-]+", "chat.messages.moderate"),
    rule("POST", r"/api/chat/mensajes/[0-9a-fA-F-]+/reacciones", "chat.messages.send"),
    rule("DELETE", r"/api/chat/mensajes/[0-9a-fA-F-]+/reacciones/[^/]+", "chat.messages.send"),
    rule("POST", r"/api/chat/conversaciones/[0-9a-fA-F-]+/leido", "chat.conversations.view"),
    # Tareas: literales antes de /{clave}.
    rule("GET", r"/api/tareas/tablero", "tareas.items.view"),
    rule("GET", r"/api/tareas/actividad", "tareas.activity.view"),
    rule("GET", r"/api/tareas/espacios", "tareas.items.view"),
    rule("POST", r"/api/tareas/espacios", "tareas.spaces.manage"),
    rule("PATCH", r"/api/tareas/espacios/[0-9a-fA-F-]+", "tareas.spaces.manage"),
    rule("GET", r"/api/tareas/config/tipos-regla", "tareas.config.view"),
    rule("GET", r"/api/tareas/config", "tareas.config.view"),
    rule("GET", r"/api/tareas/config/campos", "tareas.config.view"),
    rule("POST", r"/api/tareas/config/campos", "tareas.fields.manage"),
    rule("PATCH", r"/api/tareas/config/campos/[0-9a-fA-F-]+", "tareas.fields.manage"),
    rule("DELETE", r"/api/tareas/config/campos/[0-9a-fA-F-]+", "tareas.fields.manage"),
    rule("POST", r"/api/tareas/config/reglas", "tareas.workflow.manage"),
    rule("PATCH", r"/api/tareas/config/reglas/[0-9a-fA-F-]+", "tareas.workflow.manage"),
    rule("DELETE", r"/api/tareas/config/reglas/[0-9a-fA-F-]+", "tareas.workflow.manage"),
    # Tipos de actividad: se consultan para crear (todo el equipo) y se editan en configuración.
    rule("GET", r"/api/tareas/config/tipos", "tareas.items.view"),
    rule("POST", r"/api/tareas/config/tipos", "tareas.workflow.manage"),
    rule("PATCH", r"/api/tareas/config/tipos/[0-9a-fA-F-]+", "tareas.workflow.manage"),
    rule("DELETE", r"/api/tareas/config/tipos/[0-9a-fA-F-]+", "tareas.workflow.manage"),
    rule("GET", r"/api/tareas/config/flujos", "tareas.config.view"),
    rule("POST", r"/api/tareas/config/flujos", "tareas.workflow.manage"),
    rule("PATCH", r"/api/tareas/config/flujos/[0-9a-fA-F-]+", "tareas.workflow.manage"),
    rule("PUT", r"/api/tareas/config/flujos/[0-9a-fA-F-]+/estados", "tareas.workflow.manage"),
    rule("PUT", r"/api/tareas/config/flujos/[0-9a-fA-F-]+/transiciones", "tareas.workflow.manage"),
    rule("GET", r"/api/tareas/config/esquemas-permisos", "tareas.config.view"),
    rule("POST", r"/api/tareas/config/esquemas-permisos", "tareas.permissions.manage"),
    rule("PUT", r"/api/tareas/config/esquemas-permisos/[0-9a-fA-F-]+/reglas", "tareas.permissions.manage"),
    rule("GET", r"/api/tareas/progreso", "tareas.activity.view"),
    # Trazabilidad: el selector de vínculo y la consulta inversa por código.
    rule("GET", r"/api/tareas/objetos", "tareas.items.view"),
    rule("GET", r"/api/tareas/objeto/[^/]+", "tareas.items.view"),
    rule("GET", r"/api/tareas", "tareas.items.view"),
    rule("POST", r"/api/tareas", "tareas.items.create"),
    rule("GET", r"/api/tareas/[A-Z]{2,6}-\d+", "tareas.items.view"),
    rule("PATCH", r"/api/tareas/[A-Z]{2,6}-\d+", "tareas.items.update"),
    rule("DELETE", r"/api/tareas/[A-Z]{2,6}-\d+", "tareas.items.delete"),
    rule("GET", r"/api/tareas/[A-Z]{2,6}-\d+/transiciones", "tareas.items.transition"),
    rule("POST", r"/api/tareas/[A-Z]{2,6}-\d+/transiciones/[0-9a-fA-F-]+", "tareas.items.transition"),
    rule("PUT", r"/api/tareas/[A-Z]{2,6}-\d+/asignado", "tareas.items.assign"),
    rule("POST", r"/api/tareas/[A-Z]{2,6}-\d+/comentarios", "tareas.comments.create"),
    rule("PATCH", r"/api/tareas/[A-Z]{2,6}-\d+/comentarios/[0-9a-fA-F-]+", "tareas.comments.create"),
    rule("DELETE", r"/api/tareas/[A-Z]{2,6}-\d+/comentarios/[0-9a-fA-F-]+", "tareas.comments.moderate"),
    rule("POST", r"/api/tareas/[A-Z]{2,6}-\d+/adjuntos", "tareas.attachments.manage"),
    rule("DELETE", r"/api/tareas/[A-Z]{2,6}-\d+/adjuntos/[0-9a-fA-F-]+", "tareas.attachments.manage"),
    # IA: configuración literal antes de IDs de conversación.
    rule("GET", r"/api/ia/configuracion", "ia.config.view"),
    rule("PUT", r"/api/ia/configuracion", "ia.config.manage"),
    rule("POST", r"/api/ia/configuracion/probar", "ia.config.manage"),
    rule("GET", r"/api/ia/politicas", "ia.config.view"),
    rule("PUT", r"/api/ia/politicas/[0-9a-fA-F-]+", "ia.policy.manage"),
    rule("GET", r"/api/ia/conectores", "ia.config.view"),
    rule("POST", r"/api/ia/conectores", "ia.connectors.manage"),
    rule("PATCH", r"/api/ia/conectores/[0-9a-fA-F-]+", "ia.connectors.manage"),
    rule("GET", r"/api/ia/uso", "ia.usage.view"),
    rule("GET", r"/api/ia/conversaciones", "ia.agent.ask"),
    rule("POST", r"/api/ia/conversaciones", "ia.conversations.manage"),
    rule("GET", r"/api/ia/conversaciones/[0-9a-fA-F-]+", "ia.agent.ask"),
    rule("DELETE", r"/api/ia/conversaciones/[0-9a-fA-F-]+", "ia.conversations.manage"),
    rule("POST", r"/api/ia/conversaciones/[0-9a-fA-F-]+/mensajes", "ia.agent.ask"),
    rule("POST", r"/api/ia/conversaciones/[0-9a-fA-F-]+/cancelar", "ia.agent.ask"),
    rule("PUT", r"/api/ia/conversaciones/[0-9a-fA-F-]+/shell", "ia.shell.execute"),
    rule("POST", r"/api/ia/llamadas/[0-9a-fA-F-]+/(aprobar|rechazar)", "ia.agent.act"),
    rule("GET", r"/media/profiles/.*", None),
    rule("GET", r"/media/.*", "media.files.view"),
    rule("GET", r"/biblioteca/.*", "biblioteca.documents.view"),
)

PUBLIC_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/refresh"),
        ("POST", "/api/auth/bootstrap"),
        ("GET", "/api/auth/registration-status"),
    }
)

PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static/",
)


def find_acl_rule(method: str, path: str) -> AclRule | None:
    for item in ACL_RULES:
        if item.matches(method, path):
            return item
    return None


def permission_payload(item: PermissionDef) -> dict:
    data = asdict(item)
    data["endpoints"] = [
        {"method": rule_item.method, "pattern": rule_item.pattern}
        for rule_item in ACL_RULES
        if rule_item.permission == item.key
    ]
    return data
