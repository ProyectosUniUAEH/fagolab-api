"""Cifrado de secretos del agente con una clave separada por entorno."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import settings


def _master_key() -> bytes:
    configured = settings.AGENT_ENCRYPTION_KEY.strip() or os.environ.get("APP_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    if settings.ENV.lower() not in {"local", "dev", "development"}:
        raise RuntimeError("AGENT_ENCRYPTION_KEY es obligatoria fuera del entorno local.")
    root = Path(__file__).resolve().parent.parent
    path = root / "_private" / "agent_encryption_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value.encode("utf-8")
    value = secrets.token_urlsafe(64)
    path.write_text(value, encoding="utf-8")
    return value.encode("utf-8")


def _fernet() -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"fagolab-agent-secrets-v1",
        info=b"database-provider-credentials",
    ).derive(_master_key())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> bytes:
    if not value:
        return b""
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_secret(value: bytes | memoryview | None) -> str:
    if not value:
        return ""
    raw = bytes(value)
    return _fernet().decrypt(raw).decode("utf-8")


def secret_hint(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return f"{value[:2]}…{value[-2:]}"
    return f"{value[:3]}…{value[-4:]}"
