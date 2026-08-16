"""Registro canónico y evaluación de reglas configurables del flujo de tareas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable


@dataclass
class RuleContext:
    task: dict
    user: dict
    permissions: set[str]
    custom_fields: dict[str, Any] = field(default_factory=dict)
    comments: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    open_subtasks: int = 0
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RuleResult:
    ok: bool
    message: str = ""
    changes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleType:
    tipo: str
    fase: str
    nombre: str
    descripcion: str
    esquema: dict
    handler: Callable[[RuleContext, dict], RuleResult]


def _ok() -> RuleResult:
    return RuleResult(True)


def _same(value: Any, expected: Any) -> bool:
    return str(value or "").strip().lower() == str(expected or "").strip().lower()


def _solo_asignado(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(ctx.task.get("idAsignado") == ctx.user.get("id"))


def _solo_reportador(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(ctx.task.get("idReportador") == ctx.user.get("id"))


def _solo_rol(ctx: RuleContext, cfg: dict) -> RuleResult:
    roles = {r.get("clave") for r in ctx.user.get("roles", [])}
    return RuleResult(cfg.get("rol") in roles)


def _solo_grupo(ctx: RuleContext, cfg: dict) -> RuleResult:
    groups = {g.get("clave") for g in ctx.user.get("groups", [])}
    return RuleResult(cfg.get("grupo") in groups)


def _requiere_permiso(ctx: RuleContext, cfg: dict) -> RuleResult:
    return RuleResult(cfg.get("permiso") in ctx.permissions)


def _campo_igual(ctx: RuleContext, cfg: dict) -> RuleResult:
    return RuleResult(_same(ctx.task.get(cfg.get("campo", "")), cfg.get("valor")))


def _campo_personalizado_igual(ctx: RuleContext, cfg: dict) -> RuleResult:
    return RuleResult(_same(ctx.custom_fields.get(cfg.get("campo", "")), cfg.get("valor")))


def _sin_subtareas(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(ctx.open_subtasks == 0)


def _tiene_adjunto(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(bool(ctx.attachments))


def _required(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _campo_requerido(ctx: RuleContext, cfg: dict) -> RuleResult:
    return RuleResult(_required(ctx.task.get(cfg.get("campo", ""))))


def _campo_personalizado_requerido(ctx: RuleContext, cfg: dict) -> RuleResult:
    return RuleResult(_required(ctx.custom_fields.get(cfg.get("campo", ""))))


def _comentario_requerido(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(_required(ctx.payload.get("comentario")))


def _asignado_requerido(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(_required(ctx.task.get("idAsignado")))


def _subtareas_completas(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(ctx.open_subtasks == 0)


def _fecha_limite_futura(ctx: RuleContext, _cfg: dict) -> RuleResult:
    raw = ctx.task.get("fechaLimite")
    if not raw:
        return RuleResult(False)
    parsed = raw if isinstance(raw, (date, datetime)) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if isinstance(parsed, datetime):
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return RuleResult(parsed > datetime.now(timezone.utc))
    return RuleResult(parsed >= date.today())


def _adjunto_requerido(ctx: RuleContext, _cfg: dict) -> RuleResult:
    return RuleResult(bool(ctx.attachments))


def _change(key: str, value: Any) -> RuleResult:
    return RuleResult(True, changes={key: value})


def _asignar_a(_ctx: RuleContext, cfg: dict) -> RuleResult:
    return _change("idAsignado", cfg.get("idUsuario"))


def _establecer_campo(_ctx: RuleContext, cfg: dict) -> RuleResult:
    return _change(cfg.get("campo", ""), cfg.get("valor"))


def _establecer_personalizado(_ctx: RuleContext, cfg: dict) -> RuleResult:
    return _change(f"custom:{cfg.get('campo', '')}", cfg.get("valor"))


def _agregar_comentario(_ctx: RuleContext, cfg: dict) -> RuleResult:
    return _change("comment", cfg.get("texto", ""))


def _agregar_observador(_ctx: RuleContext, cfg: dict) -> RuleResult:
    return _change("observer", cfg.get("idUsuario"))


def _cerrar_tarea(_ctx: RuleContext, _cfg: dict) -> RuleResult:
    return _change("cerrada", True)


def _signal(kind: str) -> Callable[[RuleContext, dict], RuleResult]:
    return lambda _ctx, cfg: _change(kind, cfg or True)


OBJ = {"type": "object", "properties": {}, "additionalProperties": False}


def _schema(name: str, *, enum: list[str] | None = None) -> dict:
    prop: dict[str, Any] = {"type": "string", "title": name}
    if enum:
        prop["enum"] = enum
    return {"type": "object", "required": [name], "properties": {name: prop}, "additionalProperties": False}


RULE_TYPES: tuple[RuleType, ...] = (
    RuleType("solo_asignado", "condicion", "Solo asignado", "Disponible para la persona asignada.", OBJ, _solo_asignado),
    RuleType("solo_reportador", "condicion", "Solo reportador", "Disponible para quien reportó.", OBJ, _solo_reportador),
    RuleType("solo_rol", "condicion", "Solo rol", "Exige un rol.", _schema("rol"), _solo_rol),
    RuleType("solo_grupo", "condicion", "Solo grupo", "Exige pertenecer a un grupo.", _schema("grupo"), _solo_grupo),
    RuleType("requiere_permiso", "condicion", "Requiere permiso", "Exige un permiso ACL.", _schema("permiso"), _requiere_permiso),
    RuleType("campo_igual_a", "condicion", "Campo igual", "Compara un campo estándar.", {"type": "object", "required": ["campo", "valor"], "properties": {"campo": {"type": "string"}, "valor": {}}}, _campo_igual),
    RuleType("campo_personalizado_igual_a", "condicion", "Campo personalizado igual", "Compara un campo personalizado.", {"type": "object", "required": ["campo", "valor"], "properties": {"campo": {"type": "string"}, "valor": {}}}, _campo_personalizado_igual),
    RuleType("sin_subtareas_abiertas", "condicion", "Sin subtareas abiertas", "No permite subtareas pendientes.", OBJ, _sin_subtareas),
    RuleType("tiene_adjunto", "condicion", "Tiene adjunto", "Exige al menos un adjunto.", OBJ, _tiene_adjunto),
    RuleType("campo_requerido", "validador", "Campo requerido", "Exige un campo estándar.", _schema("campo"), _campo_requerido),
    RuleType("campo_personalizado_requerido", "validador", "Campo personalizado requerido", "Exige un valor personalizado.", _schema("campo"), _campo_personalizado_requerido),
    RuleType("comentario_requerido", "validador", "Comentario requerido", "Exige comentario al transicionar.", OBJ, _comentario_requerido),
    RuleType("asignado_requerido", "validador", "Asignado requerido", "Exige una persona asignada.", OBJ, _asignado_requerido),
    RuleType("subtareas_completas", "validador", "Subtareas completas", "Exige cerrar subtareas.", OBJ, _subtareas_completas),
    RuleType("fecha_limite_futura", "validador", "Fecha límite futura", "La fecha límite debe ser futura.", OBJ, _fecha_limite_futura),
    RuleType("adjunto_requerido", "validador", "Adjunto requerido", "Exige al menos un adjunto.", OBJ, _adjunto_requerido),
    RuleType("asignar_a", "post_funcion", "Asignar a", "Asigna la tarea.", _schema("idUsuario"), _asignar_a),
    RuleType("establecer_campo", "post_funcion", "Establecer campo", "Actualiza un campo estándar.", {"type": "object", "required": ["campo", "valor"], "properties": {"campo": {"type": "string"}, "valor": {}}}, _establecer_campo),
    RuleType("establecer_campo_personalizado", "post_funcion", "Establecer campo personalizado", "Actualiza un campo personalizado.", {"type": "object", "required": ["campo", "valor"], "properties": {"campo": {"type": "string"}, "valor": {}}}, _establecer_personalizado),
    RuleType("agregar_comentario", "post_funcion", "Agregar comentario", "Agrega un comentario automático.", _schema("texto"), _agregar_comentario),
    RuleType("agregar_observador", "post_funcion", "Agregar observador", "Suscribe a una persona.", _schema("idUsuario"), _agregar_observador),
    RuleType("cerrar_tarea", "post_funcion", "Cerrar tarea", "Registra la fecha de resolución.", OBJ, _cerrar_tarea),
    RuleType("registrar_actividad", "post_funcion", "Registrar actividad", "Registra el cambio.", OBJ, _signal("activity")),
    RuleType("notificar_observadores", "post_funcion", "Notificar observadores", "Genera notificaciones.", OBJ, _signal("notify")),
    RuleType("emitir_evento_realtime", "post_funcion", "Emitir evento", "Publica el cambio en tiempo real.", OBJ, _signal("realtime")),
    RuleType("crear_mensaje_chat", "post_funcion", "Crear mensaje en chat", "Publica un mensaje de sistema.", _schema("texto"), _signal("chat")),
)

RULE_BY_TYPE = {item.tipo: item for item in RULE_TYPES}


def public_catalog() -> list[dict]:
    return [
        {
            "tipo": item.tipo,
            "fase": item.fase,
            "nombre": item.nombre,
            "descripcion": item.descripcion,
            "esquema": item.esquema,
        }
        for item in RULE_TYPES
    ]


def evaluate(rule: dict, context: RuleContext) -> RuleResult:
    item = RULE_BY_TYPE.get(rule.get("tipo"))
    if not item:
        return RuleResult(False, f"Tipo de regla desconocido: {rule.get('tipo')}")
    result = item.handler(context, rule.get("configuracion") or {})
    if not result.ok:
        return RuleResult(False, rule.get("mensajeError") or result.message or item.descripcion)
    return result
