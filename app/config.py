"""Configuración por variables de entorno.

Contrato Kaanbal: en el cluster, el binding de BD llega como variables de entorno
inyectadas por el Secret del overlay (POSTGRES_* o {ALIAS}_*). En local, las mismas
variables se cargan desde _private/.env.dev (gitignored) — mismo contrato, mismo código.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _load_local_env() -> None:
    """Carga _private/.env.dev en os.environ si existe (solo para correr en local).

    En Kaanbal este archivo no existe y las vars vienen del Secret del pod.
    No sobreescribe variables ya presentes en el entorno.
    """
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "_private" / ".env.dev", root / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
            break


_load_local_env()


def _first_env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def _auth_secret() -> str:
    configured = _first_env("AUTH_JWT_SECRET", "JWT_SECRET", "APP_SECRET")
    if configured:
        return configured
    env = _first_env("APP_ENV", "DEPLOY_ENV", default="local").lower()
    if env not in {"local", "development", "dev"}:
        raise RuntimeError("AUTH_JWT_SECRET es obligatorio fuera del entorno local.")
    root = Path(__file__).resolve().parent.parent
    secret_path = root / "_private" / "auth_jwt_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    generated = secrets.token_urlsafe(64)
    secret_path.write_text(generated, encoding="utf-8")
    return generated


class Settings:
    # Acepta POSTGRES_* (local) o el alias de Kaanbal (FAGOLAB_BD_* / FAGO_BD_POSTGRES_*).
    PG_HOST: str = _first_env(
        "POSTGRES_HOST", "FAGOLAB_BD_HOST", "FAGO_BD_POSTGRES_HOST", default="localhost"
    )
    PG_PORT: str = _first_env(
        "POSTGRES_PORT", "FAGOLAB_BD_PORT", "FAGO_BD_POSTGRES_PORT", default="5432"
    )
    PG_DB: str = _first_env(
        "POSTGRES_DB", "FAGOLAB_BD_DATABASE", "FAGO_BD_POSTGRES_DB", default="fago_bd_postgres"
    )
    PG_USER: str = _first_env(
        "POSTGRES_USER", "FAGOLAB_BD_USER", "FAGO_BD_POSTGRES_USER", default="devadmin"
    )
    PG_PASSWORD: str = _first_env(
        "POSTGRES_PASSWORD", "FAGOLAB_BD_PASSWORD", "FAGO_BD_POSTGRES_PASSWORD", default=""
    )

    ENV: str = _first_env("APP_ENV", "DEPLOY_ENV", default="local")
    # Orígenes permitidos para CORS (coma-separados). En local incluye el Vite dev server.
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in _first_env(
            "CORS_ORIGINS",
            default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,https://fagolab.softwarefactory.site",
        ).split(",")
        if origin.strip()
    ]

    # Carpeta para almacenar imágenes subidas (media). En Kaanbal montar un volumen o usar S3/Minio.
    MEDIA_DIR: str = _first_env("MEDIA_DIR", default=str(Path(__file__).resolve().parent.parent / "_media"))

    AUTH_JWT_SECRET: str = _auth_secret()
    AUTH_JWT_ISSUER: str = _first_env("AUTH_JWT_ISSUER", default="fagolab")
    AUTH_ACCESS_MINUTES: int = int(_first_env("AUTH_ACCESS_MINUTES", default="15"))
    AUTH_REFRESH_DAYS: int = int(_first_env("AUTH_REFRESH_DAYS", default="7"))
    AUTH_COOKIE_SECURE: bool = _first_env(
        "AUTH_COOKIE_SECURE",
        default="true" if ENV.lower() in {"prod", "production"} else "false",
    ).lower() in {"1", "true", "yes", "si"}
    AUTH_MAX_FAILED_ATTEMPTS: int = int(_first_env("AUTH_MAX_FAILED_ATTEMPTS", default="5"))
    AUTH_LOCK_MINUTES: int = int(_first_env("AUTH_LOCK_MINUTES", default="15"))
    AUTH_TRUST_PROXY_HEADERS: bool = _first_env(
        "AUTH_TRUST_PROXY_HEADERS",
        default="false",
    ).lower() in {"1", "true", "yes", "si"}

    AGENT_ENCRYPTION_KEY: str = _first_env("AGENT_ENCRYPTION_KEY")
    AGENT_SHELL_ENABLED: bool = _first_env("AGENT_SHELL_ENABLED", default="false").lower() in {
        "1", "true", "yes", "si",
    }
    AGENT_SHELL_ALLOW_REMOTE: bool = _first_env(
        "AGENT_SHELL_ALLOW_REMOTE", default="false",
    ).lower() in {"1", "true", "yes", "si"}
    AGENT_SHELL_WORKDIR: str = _first_env(
        "AGENT_SHELL_WORKDIR",
        default=str(Path(__file__).resolve().parent.parent.parent),
    )
    AGENT_SHELL_TIMEOUT_S: int = int(_first_env("AGENT_SHELL_TIMEOUT_S", default="30"))
    AGENT_HTTP_TIMEOUT_S: int = int(_first_env("AGENT_HTTP_TIMEOUT_S", default="20"))

    @property
    def dsn(self) -> str:
        return (
            f"host={self.PG_HOST} port={self.PG_PORT} dbname={self.PG_DB} "
            f"user={self.PG_USER} password={self.PG_PASSWORD} connect_timeout=15"
        )

    @property
    def db_configured(self) -> bool:
        return bool(self.PG_HOST and self.PG_PASSWORD)


settings = Settings()
