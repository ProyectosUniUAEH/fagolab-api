"""Criptografía, JWT, cookies y middleware de autorización."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import secrets
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from . import repo_auth
from .auth_permissions import PUBLIC_PATHS, PUBLIC_PREFIXES, find_acl_rule
from .config import settings


ACCESS_COOKIE = "fago_access"
REFRESH_COOKIE = "fago_refresh"
CSRF_COOKIE = "fago_csrf"
JWT_ALGORITHM = "HS256"
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        return password_hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(encoded: str) -> bool:
    try:
        return password_hasher.check_needs_rehash(encoded)
    except (InvalidHashError, TypeError):
        return True


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError("La contraseña debe tener al menos 12 caracteres.")
    if len(password) > 128:
        raise ValueError("La contraseña es demasiado larga.")
    categories = (
        any(ch.islower() for ch in password),
        any(ch.isupper() for ch in password),
        any(ch.isdigit() for ch in password),
        any(not ch.isalnum() for ch in password),
    )
    if sum(categories) < 3:
        raise ValueError("Usa mayúsculas, minúsculas, números y/o símbolos (al menos 3 tipos).")


def make_access_token(user_id: str, session_id: str) -> str:
    issued = now_utc()
    payload = {
        "sub": user_id,
        "sid": session_id,
        "iss": settings.AUTH_JWT_ISSUER,
        "iat": issued,
        "nbf": issued,
        "exp": issued + timedelta(minutes=settings.AUTH_ACCESS_MINUTES),
        "jti": str(uuid.uuid4()),
        "type": "access",
    }
    return jwt.encode(payload, settings.AUTH_JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.AUTH_JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        issuer=settings.AUTH_JWT_ISSUER,
        options={"require": ["sub", "sid", "iss", "iat", "exp", "jti", "type"]},
    )


def new_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    common = {
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": "lax",
    }
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        httponly=True,
        max_age=settings.AUTH_ACCESS_MINUTES * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        httponly=True,
        max_age=settings.AUTH_REFRESH_DAYS * 86400,
        path="/api/auth",
        **common,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        max_age=settings.AUTH_REFRESH_DAYS * 86400,
        path="/",
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


def validate_csrf(request: Request) -> bool:
    cookie_value = request.cookies.get(CSRF_COOKIE, "")
    header_value = request.headers.get("X-CSRF-Token", "")
    return bool(cookie_value and header_value and hmac.compare_digest(cookie_value, header_value))


def request_ip(request: Request) -> str | None:
    candidate = request.client.host if request.client else None
    if settings.AUTH_TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def request_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise RuntimeError("No hay usuario autenticado en la solicitud.")
    return user


class AuthAclMiddleware(BaseHTTPMiddleware):
    """Autentica, aplica ACL fail-closed y audita mutaciones."""

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        if method == "OPTIONS" or path in {"/", "/test-ws"} or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = correlation_id
            return response

        if (method, path) in PUBLIC_PATHS:
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = correlation_id
            return response

        protected = path.startswith("/api/") or path.startswith("/media/") or path.startswith("/biblioteca/")
        if not protected:
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = correlation_id
            return response

        token = request.cookies.get(ACCESS_COOKIE)
        authorization = request.headers.get("authorization", "")
        if not token and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            return JSONResponse(
                {"detail": "Debes iniciar sesión.", "code": "not_authenticated"},
                status_code=401,
                headers={"X-Correlation-Id": correlation_id},
            )

        try:
            claims = decode_access_token(token)
            if claims.get("type") != "access":
                raise jwt.InvalidTokenError("Tipo de token inválido.")
            user = repo_auth.get_user_by_id(claims["sub"])
            session = repo_auth.get_active_session(claims["sid"], claims["sub"])
            if not user or not session or not user.get("activo") or user.get("status") != "activa":
                raise jwt.InvalidTokenError("Sesión inactiva.")
        except jwt.ExpiredSignatureError:
            return JSONResponse(
                {"detail": "La sesión necesita renovarse.", "code": "token_expired"},
                status_code=401,
                headers={"X-Correlation-Id": correlation_id},
            )
        except (jwt.InvalidTokenError, KeyError, ValueError):
            return JSONResponse(
                {"detail": "La sesión no es válida.", "code": "invalid_session"},
                status_code=401,
                headers={"X-Correlation-Id": correlation_id},
            )

        permissions = repo_auth.effective_permissions(user["id"], bool(user.get("isSuperadmin")))
        user["permissions"] = permissions
        request.state.user = user
        request.state.session_id = claims["sid"]
        request.state.permission = None

        acl_rule = find_acl_rule(method, path)
        if acl_rule is None:
            repo_auth.audit(
                event_type="acl_denied",
                action="access",
                resource=path,
                actor_id=user["id"],
                session_id=claims["sid"],
                actor_name=user["nombre"],
                method=method,
                path=path,
                status_code=403,
                success=False,
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
                correlation_id=correlation_id,
                details={"reason": "endpoint_not_registered"},
            )
            return JSONResponse(
                {"detail": "Endpoint sin permiso ACL registrado.", "code": "acl_unregistered"},
                status_code=403,
                headers={"X-Correlation-Id": correlation_id},
            )

        request.state.permission = acl_rule.permission
        if acl_rule.permission and not user.get("isSuperadmin") and acl_rule.permission not in permissions:
            repo_auth.audit(
                event_type="acl_denied",
                action="access",
                resource=path,
                actor_id=user["id"],
                session_id=claims["sid"],
                actor_name=user["nombre"],
                permission=acl_rule.permission,
                method=method,
                path=path,
                status_code=403,
                success=False,
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
                correlation_id=correlation_id,
                details={"reason": "permission_missing"},
            )
            return JSONResponse(
                {"detail": "No tienes permiso para realizar esta acción.", "code": "permission_denied"},
                status_code=403,
                headers={"X-Correlation-Id": correlation_id},
            )

        if method in {"POST", "PUT", "PATCH", "DELETE"} and not validate_csrf(request):
            return JSONResponse(
                {"detail": "Validación CSRF fallida.", "code": "csrf_invalid"},
                status_code=403,
                headers={"X-Correlation-Id": correlation_id},
            )

        repo_auth.touch_user(user["id"])
        audit_http_mutation = method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/api/auth/page-view"
        try:
            response = await call_next(request)
        except Exception:
            if audit_http_mutation:
                repo_auth.audit(
                    event_type="http_mutation",
                    action=method.lower(),
                    resource=path,
                    actor_id=user["id"],
                    session_id=claims["sid"],
                    actor_name=user["nombre"],
                    permission=acl_rule.permission,
                    method=method,
                    path=path,
                    status_code=500,
                    success=False,
                    ip=request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    correlation_id=correlation_id,
                )
            raise
        response.headers["X-Correlation-Id"] = correlation_id
        if audit_http_mutation:
            repo_auth.audit(
                event_type="http_mutation",
                action=method.lower(),
                resource=path,
                actor_id=user["id"],
                session_id=claims["sid"],
                actor_name=user["nombre"],
                permission=acl_rule.permission,
                method=method,
                path=path,
                status_code=response.status_code,
                success=response.status_code < 400,
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
                correlation_id=correlation_id,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Encabezados defensivos compatibles con la app local y el despliegue web."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        if request.url.path.startswith("/api/auth"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.ENV.lower() in {"prod", "production"}:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
