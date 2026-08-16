"""Loop del agente, streaming por el bus compartido y aprobaciones mutantes."""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
from typing import Any

from .deepseek import stream_chat
from .registry import assert_available, available
from . import tools as _registered_tools  # noqa: F401
from .. import repo_auth, repo_ia
from ..realtime import realtime_bus


_runs: dict[str, asyncio.Task] = {}
_approvals: dict[str, asyncio.Future[bool]] = {}


async def _publish(conversation_id: str, event_type: str, **payload) -> None:
    channel = f"ia:conversacion:{conversation_id}"
    await realtime_bus.publish(channel, {"type": event_type, "channel": channel, **payload})


async def _db(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def _cost(config: dict, input_tokens: int, output_tokens: int) -> float:
    prices = config.get("precios") or {}
    input_price = float(prices.get("inputPerMillion") or prices.get("entrada") or 0)
    output_price = float(prices.get("outputPerMillion") or prices.get("salida") or 0)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def _merge_tool_delta(accumulator: dict[int, dict], chunks: list[dict]) -> None:
    for fragment in chunks or []:
        index = int(fragment.get("index", 0))
        item = accumulator.setdefault(
            index,
            {"id": fragment.get("id") or "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if fragment.get("id"):
            item["id"] = fragment["id"]
        function = fragment.get("function") or {}
        item["function"]["name"] += function.get("name") or ""
        item["function"]["arguments"] += function.get("arguments") or ""


async def _execute_tool(
    *,
    conversation_id: str,
    run_id: str,
    call_spec: dict,
    context: dict,
    mode: str,
) -> dict:
    name = call_spec.get("function", {}).get("name", "")
    raw_arguments = call_spec.get("function", {}).get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        arguments = {}
    try:
        tool = assert_available(name, context, mode, context.get("policy"))
    except PermissionError:
        return {"error": "permiso_denegado", "herramienta": name}
    call = await _db(repo_ia.create_tool_call, run_id, name, arguments, tool.mutante)
    if tool.mutante:
        await _publish(
            conversation_id,
            "ia.tool.propuesta",
            runId=run_id,
            llamada=call,
            riesgo=tool.riesgo,
            descripcion=tool.descripcion,
        )
        future = asyncio.get_running_loop().create_future()
        _approvals[call["id"]] = future
        try:
            approved = await asyncio.wait_for(future, timeout=180)
        except TimeoutError:
            await _db(repo_ia.complete_tool_call, call["id"], "expirada", None, "Aprobación expirada.")
            return {"error": "aprobacion_expirada"}
        finally:
            _approvals.pop(call["id"], None)
        if not approved:
            return {"error": "rechazado_por_usuario"}
    try:
        # Revalidar inmediatamente antes de tocar datos.
        tool = assert_available(name, context, mode, context.get("policy"))
        result = await tool.handler(arguments, context)
        if name == "sistema.ejecutar_comando":
            await _db(
                repo_ia.record_shell,
                run_id,
                call["id"],
                str(arguments.get("comando") or ""),
                arguments.get("cwd"),
                result,
            )
            await _db(
                repo_auth.audit,
                event_type="ia_shell",
                action="execute",
                resource="sistema.ejecutar_comando",
                actor_id=context.get("id"),
                actor_name=context.get("nombre"),
                permission="ia.shell.execute",
                success=result.get("exitCode") == 0 and not result.get("timeout"),
                details={
                    "argv": result.get("argv"),
                    "durationMs": result.get("durationMs"),
                    "truncado": result.get("truncado"),
                    "timeout": result.get("timeout"),
                },
            )
        await _db(repo_ia.complete_tool_call, call["id"], "ejecutada", result, None)
        await _publish(
            conversation_id,
            "ia.tool.resultado",
            runId=run_id,
            llamadaId=call["id"],
            nombre=name,
            resultado=result,
        )
        return result
    except PermissionError as exc:
        result = {"error": "permiso_denegado", "detalle": str(exc)}
        await _db(repo_ia.complete_tool_call, call["id"], "error", result, str(exc))
        return result
    except Exception as exc:
        result = {"error": "herramienta_error", "detalle": str(exc)}
        await _db(repo_ia.complete_tool_call, call["id"], "error", result, str(exc))
        return result


async def run_agent(run: dict, conversation_id: str, user: dict) -> None:
    run_id = run["id"]
    current = asyncio.current_task()
    if current:
        _runs[run_id] = current
    try:
        context = await _db(repo_ia.runtime_context, conversation_id, user)
        config = context["config"]
        mode = context["mode"]
        if not config.get("habilitado") or not config.get("apiKey"):
            raise RuntimeError("El agente no está habilitado o no tiene llave configurada.")
        filtered_tools = available(context, mode, context.get("policy"))
        serialized_tools = [item.deepseek() for item in filtered_tools]
        await _publish(
            conversation_id,
            "ia.run.started",
            runId=run_id,
            herramientas=[item.nombre for item in filtered_tools],
            modo=mode,
        )
        max_iterations = min(
            int(config.get("maxIteraciones") or 8),
            int(context.get("policy", {}).get("maxIteraciones") or 8),
        )
        total_input = total_output = 0
        final_content = ""
        for iteration in range(max_iterations):
            messages = await _db(repo_ia.model_messages, conversation_id, 60)
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_accumulator: dict[int, dict] = {}
            usage: dict = {}
            async for chunk in stream_chat(
                base_url=config["baseUrl"],
                api_key=config["apiKey"],
                model=config["modelo"],
                messages=messages,
                tools=serialized_tools,
                temperature=float(config.get("temperatura", 0.2)),
            ):
                usage = chunk.get("usage") or usage
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    await _publish(conversation_id, "ia.delta", runId=run_id, delta=delta["content"])
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    reasoning_parts.append(reasoning)
                    await _publish(conversation_id, "ia.razonamiento", runId=run_id, delta=reasoning)
                _merge_tool_delta(tool_accumulator, delta.get("tool_calls") or [])
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
            total_input += input_tokens
            total_output += output_tokens
            content = "".join(content_parts)
            tool_calls = [tool_accumulator[index] for index in sorted(tool_accumulator)]
            await _db(
                repo_ia.add_message,
                conversation_id,
                "assistant",
                content,
                tool_calls=tool_calls,
                metadata={"reasoning": "".join(reasoning_parts), "iteration": iteration},
            )
            if not tool_calls:
                final_content = content
                break
            for call_spec in tool_calls:
                result = await _execute_tool(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    call_spec=call_spec,
                    context=context,
                    mode=mode,
                )
                await _db(
                    repo_ia.add_message,
                    conversation_id,
                    "tool",
                    json.dumps(result, ensure_ascii=False, default=str),
                    metadata={"toolCallId": call_spec.get("id"), "name": call_spec.get("function", {}).get("name")},
                )
        cost = _cost(config, total_input, total_output)
        await _db(repo_ia.add_usage, conversation_id, total_input, total_output, cost)
        await _db(repo_ia.finish_run, run_id, "completada", None)
        await _publish(
            conversation_id,
            "ia.uso",
            runId=run_id,
            tokensEntrada=total_input,
            tokensSalida=total_output,
            costo=cost,
        )
        await _publish(conversation_id, "ia.mensaje", runId=run_id, contenido=final_content)
        await _publish(conversation_id, "ia.run.finished", runId=run_id, estado="completada")
    except asyncio.CancelledError:
        await _db(repo_ia.finish_run, run_id, "cancelada", None)
        await _publish(conversation_id, "ia.run.finished", runId=run_id, estado="cancelada")
        raise
    except Exception as exc:
        await _db(repo_ia.finish_run, run_id, "error", str(exc))
        await _publish(conversation_id, "ia.error", runId=run_id, detalle=str(exc))
        await _publish(conversation_id, "ia.run.finished", runId=run_id, estado="error")
    finally:
        _runs.pop(run_id, None)


def start_run(run: dict, conversation_id: str, user: dict) -> None:
    task = asyncio.create_task(run_agent(run, conversation_id, user))
    _runs[run["id"]] = task


def cancel_run(run_id: str) -> bool:
    task = _runs.get(run_id)
    if not task or task.done():
        return False
    task.cancel()
    return True


def resolve_approval(call_id: str, approved: bool) -> bool:
    future = _approvals.get(call_id)
    if not future or future.done():
        return False
    future.set_result(approved)
    return True
