"""Ejecución local explícita y acotada para el modo Super."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shlex
import time

from ..config import settings


SAFE_ENV_KEYS = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}
MAX_OUTPUT = 64 * 1024


async def execute(command: str, cwd: str | None, blocked_patterns: list[str]) -> dict:
    if not settings.AGENT_SHELL_ENABLED:
        raise PermissionError("El interruptor global del shell está apagado.")
    if settings.ENV.lower() not in {"local", "dev", "development"} and not settings.AGENT_SHELL_ALLOW_REMOTE:
        raise PermissionError("El shell solo está permitido en local.")
    if any(re.search(pattern, command, re.IGNORECASE) for pattern in blocked_patterns):
        raise PermissionError("El comando coincide con una regla bloqueada.")
    argv = shlex.split(command, posix=os.name != "nt")
    if not argv:
        raise ValueError("Comando vacío.")
    root = Path(settings.AGENT_SHELL_WORKDIR).resolve()
    workdir = Path(cwd or root).resolve()
    if not workdir.is_relative_to(root):
        raise PermissionError("El directorio está fuera del área permitida.")
    env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV_KEYS}
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(workdir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.AGENT_SHELL_TIMEOUT_S,
        )
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return {
            "argv": argv,
            "stdout": stdout[:MAX_OUTPUT].decode("utf-8", "replace"),
            "stderr": stderr[:MAX_OUTPUT].decode("utf-8", "replace"),
            "exitCode": None,
            "durationMs": int((time.monotonic() - started) * 1000),
            "truncado": len(stdout) + len(stderr) > MAX_OUTPUT,
            "timeout": True,
        }
    return {
        "argv": argv,
        "stdout": stdout[:MAX_OUTPUT].decode("utf-8", "replace"),
        "stderr": stderr[:MAX_OUTPUT].decode("utf-8", "replace"),
        "exitCode": process.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "truncado": len(stdout) + len(stderr) > MAX_OUTPUT,
        "timeout": False,
    }
