"""Endpoints de autenticación y administración de seguridad."""
from __future__ import annotations

from datetime import timedelta, timezone
import ipaddress
import os
from pathlib import Path
import re
import secrets
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, Request, UploadFile, File as UploadField
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import repo_auth
from .auth import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    hash_password,
    hash_refresh_token,
    make_access_token,
    new_csrf_token,
    new_refresh_token,
    now_utc,
    password_needs_rehash,
    request_ip,
    request_user,
    set_auth_cookies,
    validate_csrf,
    verify_password,
)
from .config import settings


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    email: str
    password: str


class RegisterPayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    cargo: str | None = Field(default=None, max_length=160)


class BootstrapPayload(RegisterPayload):
    pass


class ChangePasswordPayload(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=128)
    newPassword: str = Field(min_length=12, max_length=128)


class ProfileUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    cargo: str | None = Field(default=None, max_length=160)
    institucion: str | None = Field(default=None, max_length=200)
    departamento: str | None = Field(default=None, max_length=200)
    gradoAcademico: str | None = Field(default=None, max_length=160)
    lineaInvestigacion: str | None = Field(default=None, max_length=300)
    biografia: str | None = Field(default=None, max_length=1000)
    orcid: str | None = Field(default=None, max_length=19)
    telefono: str | None = Field(default=None, max_length=40)
    ubicacion: str | None = Field(default=None, max_length=160)
    enlacePersonal: str | None = Field(default=None, max_length=500)


class PageViewPayload(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    name: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=200)


class AdminCreateUserPayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=5, max_length=254)
    cargo: str | None = Field(default=None, max_length=160)


class AdminUpdateUserPayload(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    email: str | None = Field(default=None, min_length=5, max_length=254)
    active: bool | None = None
    status: Literal["pendiente", "activa", "suspendida"] | None = None
    cargo: str | None = Field(default=None, max_length=160)


class ApproveUserPayload(BaseModel):
    roleIds: list[str] = Field(default_factory=list, max_length=50)


class PermissionEffectPayload(BaseModel):
    permission: str = Field(min_length=3, max_length=160)
    effect: Literal["allow", "deny"]


class UserAccessPayload(BaseModel):
    roleIds: list[str] = Field(default_factory=list, max_length=50)
    groupIds: list[str] = Field(default_factory=list, max_length=100)
    permissions: list[PermissionEffectPayload] = Field(default_factory=list, max_length=250)


class SuperadminPayload(BaseModel):
    enabled: bool


class RoleCreatePayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    key: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list, max_length=250)


class RoleUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class RolePermissionsPayload(BaseModel):
    permissions: list[str] = Field(default_factory=list, max_length=250)


class GroupPayload(BaseModel):
    name: str = Field(min_length=2, max_length=140)
    key: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    members: list[str] = Field(default_factory=list, max_length=500)
    roles: list[str] = Field(default_factory=list, max_length=100)
    permissions: list[PermissionEffectPayload] = Field(default_factory=list, max_length=250)


class GroupUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=140)
    description: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class GroupAccessPayload(BaseModel):
    members: list[str] = Field(default_factory=list, max_length=500)
    roles: list[str] = Field(default_factory=list, max_length=100)
    permissions: list[PermissionEffectPayload] = Field(default_factory=list, max_length=250)


def client_data(request: Request) -> tuple[str | None, str | None]:
    return request_ip(request), request.headers.get("user-agent")


def auth_response(user: dict, session_id: str, refresh_token: str) -> JSONResponse:
    csrf = new_csrf_token()
    access = make_access_token(user["id"], session_id)
    response = JSONResponse(jsonable_encoder({"user": repo_auth.public_user(user), "csrfToken": csrf}))
    set_auth_cookies(response, access, refresh_token, csrf)
    return response


@router.get("/registration-status")
def registration_status():
    return {
        "signupEnabled": True,
        "needsBootstrap": repo_auth.active_superadmin_count() == 0,
    }


def _can_bootstrap(request: Request) -> bool:
    """En local solo loopback. En Kaanbal el cliente visible es el ingress del cluster."""
    host = (request.client.host if request.client else "") or ""
    if host in {"127.0.0.1", "::1", "localhost"}:
        return True
    if settings.ENV.lower() in {"prod", "production", "staging"}:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
        return bool(ip.is_loopback or ip.is_private)
    except ValueError:
        return False


@router.post("/bootstrap", status_code=201)
def bootstrap(payload: BootstrapPayload, request: Request):
    if not _can_bootstrap(request):
        raise HTTPException(403, "La primera administradora solo puede crearse desde la laptop servidor.")
    if any(user["isSuperadmin"] for user in repo_auth.list_users()):
        raise HTTPException(409, "La configuración inicial ya fue completada.")
    if not repo_auth.valid_email(payload.email):
        raise HTTPException(422, "Escribe un correo válido.")
    if repo_auth.get_user_by_email(payload.email):
        raise HTTPException(409, "Ya existe una cuenta con ese correo.")
    try:
        user = repo_auth.create_user(
            name=payload.name,
            email=payload.email,
            password_hash=hash_password(payload.password),
            status="activa",
            cargo=payload.cargo,
            is_superadmin=True,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ip, agent = client_data(request)
    repo_auth.audit(
        event_type="auth_bootstrap",
        action="bootstrap",
        resource="security",
        actor_id=user["id"],
        actor_name=user["nombre"],
        status_code=201,
        ip=ip,
        user_agent=agent,
    )
    return {"ok": True, "message": "Administradora principal creada. Ya puedes iniciar sesión."}


@router.post("/register", status_code=202)
def register(payload: RegisterPayload, request: Request):
    if not repo_auth.valid_email(payload.email):
        raise HTTPException(422, "Escribe un correo válido.")
    if repo_auth.get_user_by_email(payload.email):
        raise HTTPException(409, "Ya existe una cuenta con ese correo.")
    try:
        encoded = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user = repo_auth.create_user(
        name=payload.name,
        email=payload.email,
        password_hash=encoded,
        status="pendiente",
        cargo=payload.cargo,
    )
    ip, agent = client_data(request)
    repo_auth.audit(
        event_type="auth_register",
        action="register",
        resource="user",
        actor_id=user["id"],
        actor_name=user["nombre"],
        status_code=202,
        ip=ip,
        user_agent=agent,
        details={"status": "pending"},
    )
    return {"ok": True, "message": "Tu solicitud fue enviada. Una administradora debe aprobarla."}


@router.post("/login")
def login(payload: LoginPayload, request: Request):
    user = repo_auth.get_user_by_email(payload.email)
    ip, agent = client_data(request)
    if not user or not verify_password(payload.password, user.get("passwordHash")):
        if user:
            repo_auth.mark_login_failure(user["id"])
        repo_auth.audit(
            event_type="auth_login_failed",
            action="login",
            resource="session",
            actor_id=user["id"] if user else None,
            actor_name=user["nombre"] if user else payload.email.strip().lower(),
            status_code=401,
            success=False,
            ip=ip,
            user_agent=agent,
            details={"reason": "invalid_credentials"},
        )
        raise HTTPException(401, "Correo o contraseña incorrectos.")
    locked_until = user.get("lockedUntil")
    if locked_until and locked_until > now_utc():
        raise HTTPException(423, "La cuenta está bloqueada temporalmente por intentos fallidos.")
    if not user.get("activo") or user.get("status") == "suspendida":
        raise HTTPException(403, "Esta cuenta está suspendida.")
    if user.get("status") == "pendiente":
        raise HTTPException(403, "Tu registro está pendiente de aprobación.")
    if password_needs_rehash(user["passwordHash"]):
        repo_auth.set_password(user["id"], hash_password(payload.password), bool(user.get("mustChangePassword")))
        user = repo_auth.get_user_by_id(user["id"]) or user
    repo_auth.mark_login_success(user["id"])
    refresh = new_refresh_token()
    session = repo_auth.create_session(
        user["id"],
        hash_refresh_token(refresh),
        now_utc() + timedelta(days=settings.AUTH_REFRESH_DAYS),
        ip,
        agent,
    )
    repo_auth.audit(
        event_type="auth_login",
        action="login",
        resource="session",
        actor_id=user["id"],
        session_id=session["id"],
        actor_name=user["nombre"],
        status_code=200,
        ip=ip,
        user_agent=agent,
    )
    return auth_response(user, session["id"], refresh)


@router.post("/refresh")
def refresh(request: Request):
    if not validate_csrf(request):
        raise HTTPException(403, "Validación CSRF fallida.")
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise HTTPException(401, "No hay una sesión renovable.")
    old = repo_auth.get_session_by_hash(hash_refresh_token(raw_token))
    if not old:
        raise HTTPException(401, "El token de renovación no es válido.")
    if old.get("revokedAt"):
        repo_auth.revoke_family(old["familyId"], "refresh_token_reuse")
        raise HTTPException(401, "Se detectó reutilización de una sesión revocada.")
    if old["expiresAt"] <= now_utc():
        repo_auth.revoke_session(old["id"], "expired")
        raise HTTPException(401, "La sesión expiró.")
    user = repo_auth.get_user_by_id(old["userId"])
    if not user or not user.get("activo") or user.get("status") != "activa":
        repo_auth.revoke_family(old["familyId"], "user_inactive")
        raise HTTPException(401, "La cuenta ya no está activa.")
    repo_auth.revoke_session(old["id"], "rotated")
    fresh = new_refresh_token()
    ip, agent = client_data(request)
    new_session = repo_auth.create_session(
        user["id"],
        hash_refresh_token(fresh),
        now_utc() + timedelta(days=settings.AUTH_REFRESH_DAYS),
        ip,
        agent,
        old["familyId"],
    )
    repo_auth.audit(
        event_type="auth_refresh",
        action="refresh",
        resource="session",
        actor_id=user["id"],
        session_id=new_session["id"],
        actor_name=user["nombre"],
        status_code=200,
        ip=ip,
        user_agent=agent,
        details={"rotatedFrom": old["id"]},
    )
    return auth_response(user, new_session["id"], fresh)


@router.get("/me")
def me(request: Request):
    return repo_auth.public_user(request_user(request))


@router.get("/directory")
def directory():
    return repo_auth.list_directory_users()


def _valid_orcid(value: str) -> bool:
    compact = value.removeprefix("https://orcid.org/").strip()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", compact):
        return False
    digits = compact.replace("-", "")
    total = 0
    for char in digits[:15]:
        total = (total + int(char)) * 2
    result = (12 - (total % 11)) % 11
    return digits[-1] == ("X" if result == 10 else str(result))


@router.patch("/profile")
def update_profile(payload: ProfileUpdatePayload, request: Request):
    actor = request_user(request)
    values = payload.model_dump(exclude_unset=True)
    if "orcid" in values and values["orcid"]:
        values["orcid"] = values["orcid"].removeprefix("https://orcid.org/").strip()
        if not _valid_orcid(values["orcid"]):
            raise HTTPException(422, "El ORCID no tiene un formato o dígito verificador válido.")
    if "enlacePersonal" in values and values["enlacePersonal"]:
        link = values["enlacePersonal"].strip()
        if not re.fullmatch(r"https?://[^\s]+", link, re.IGNORECASE):
            raise HTTPException(422, "El enlace profesional debe comenzar con http:// o https://.")
        values["enlacePersonal"] = link
    if "telefono" in values and values["telefono"]:
        if not re.fullmatch(r"[0-9+().\-\s]{7,40}", values["telefono"]):
            raise HTTPException(422, "El teléfono contiene caracteres no válidos.")
    before = repo_auth.public_user(actor)
    try:
        user = repo_auth.update_own_profile(actor["id"], values)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    repo_auth.audit(
        event_type="profile_updated",
        action="update_profile",
        resource="user",
        actor_id=actor["id"],
        session_id=request.state.session_id,
        actor_name=user["nombre"],
        status_code=200,
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
        before={key: before.get(key) for key in values},
        after={key: user.get({
            "name": "nombre",
            "gradoAcademico": "gradoAcademico",
            "lineaInvestigacion": "lineaInvestigacion",
            "enlacePersonal": "enlacePersonal",
        }.get(key, key)) for key in values},
    )
    return user


def _detect_profile_image(raw: bytes) -> tuple[str, str] | None:
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def _delete_profile_file(storage_uri: str | None) -> None:
    prefix = "/media/profiles/"
    if not storage_uri or not storage_uri.startswith(prefix):
        return
    root = (Path(settings.MEDIA_DIR) / "profiles").resolve()
    candidate = (root / os.path.basename(storage_uri)).resolve()
    if candidate.parent == root and candidate.is_file():
        candidate.unlink()


async def _save_profile_image(kind: Literal["avatar", "portada"], file: UploadFile, request: Request):
    actor = request_user(request)
    limit = 5 * 1024 * 1024 if kind == "avatar" else 8 * 1024 * 1024
    raw = await file.read(limit + 1)
    if not raw:
        raise HTTPException(422, "Selecciona una imagen.")
    if len(raw) > limit:
        size = "5 MB" if kind == "avatar" else "8 MB"
        raise HTTPException(413, f"La imagen supera el máximo de {size}.")
    detected = _detect_profile_image(raw)
    if not detected:
        raise HTTPException(415, "Solo se aceptan imágenes JPG, PNG o WebP.")
    extension, _mime = detected
    directory = Path(settings.MEDIA_DIR) / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{kind}_{uuid.uuid4().hex}{extension}"
    destination = directory / filename
    destination.write_bytes(raw)
    storage_uri = f"/media/profiles/{filename}"
    previous = actor.get("avatarUri" if kind == "avatar" else "portadaUri")
    try:
        user = repo_auth.set_profile_image(actor["id"], kind, storage_uri)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    _delete_profile_file(previous)
    repo_auth.audit(
        event_type="profile_image_updated",
        action=f"update_{kind}",
        resource="user",
        actor_id=actor["id"],
        session_id=request.state.session_id,
        actor_name=user["nombre"],
        status_code=200,
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"kind": kind, "sizeBytes": len(raw)},
        before={"storageUri": previous},
        after={"storageUri": storage_uri},
    )
    return user


@router.post("/profile/avatar")
async def upload_avatar(request: Request, file: UploadFile = UploadField(...)):
    return await _save_profile_image("avatar", file, request)


@router.post("/profile/portada")
async def upload_cover(request: Request, file: UploadFile = UploadField(...)):
    return await _save_profile_image("portada", file, request)


@router.delete("/profile/{kind}")
def delete_profile_image(kind: Literal["avatar", "portada"], request: Request):
    actor = request_user(request)
    field = "avatarUri" if kind == "avatar" else "portadaUri"
    previous = actor.get(field)
    user = repo_auth.set_profile_image(actor["id"], kind, None)
    _delete_profile_file(previous)
    repo_auth.audit(
        event_type="profile_image_deleted",
        action=f"delete_{kind}",
        resource="user",
        actor_id=actor["id"],
        session_id=request.state.session_id,
        actor_name=user["nombre"],
        status_code=200,
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"kind": kind},
        before={"storageUri": previous},
        after={"storageUri": None},
    )
    return user


@router.post("/logout")
def logout(request: Request):
    user = request_user(request)
    session_id = request.state.session_id
    repo_auth.revoke_session(session_id, "logout")
    ip, agent = client_data(request)
    repo_auth.audit(
        event_type="auth_logout",
        action="logout",
        resource="session",
        actor_id=user["id"],
        session_id=session_id,
        actor_name=user["nombre"],
        status_code=200,
        ip=ip,
        user_agent=agent,
    )
    response = JSONResponse({"ok": True})
    clear_auth_cookies(response)
    return response


@router.post("/change-password")
def change_password(payload: ChangePasswordPayload, request: Request):
    user = request_user(request)
    stored = repo_auth.get_user_by_id(user["id"])
    if not stored or not verify_password(payload.currentPassword, stored.get("passwordHash")):
        raise HTTPException(400, "La contraseña actual no es correcta.")
    try:
        encoded = hash_password(payload.newPassword)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if verify_password(payload.newPassword, stored.get("passwordHash")):
        raise HTTPException(422, "La nueva contraseña debe ser diferente.")
    repo_auth.set_password(user["id"], encoded, False)
    repo_auth.revoke_user_sessions(user["id"], "password_changed", request.state.session_id)
    repo_auth.audit(
        event_type="auth_password_changed",
        action="change_password",
        resource="user",
        actor_id=user["id"],
        session_id=request.state.session_id,
        actor_name=user["nombre"],
        status_code=200,
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.post("/page-view", status_code=204)
def page_view(payload: PageViewPayload, request: Request):
    user = request_user(request)
    repo_auth.audit(
        event_type="page_view",
        action="view",
        resource=payload.name or payload.path,
        actor_id=user["id"],
        session_id=request.state.session_id,
        actor_name=user["nombre"],
        permission=request.state.permission,
        method="VIEW",
        path=payload.path,
        status_code=204,
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"title": payload.title},
    )
    return Response(status_code=204)


@router.get("/sessions")
def sessions(request: Request):
    return repo_auth.own_sessions(request_user(request)["id"])


@router.delete("/sessions/{session_id}")
def revoke_own_session(session_id: str, request: Request):
    user = request_user(request)
    valid_ids = {item["id"] for item in repo_auth.own_sessions(user["id"])}
    if session_id not in valid_ids:
        raise HTTPException(404, "Sesión no encontrada.")
    repo_auth.revoke_session(session_id, "user_revoked")
    return {"ok": True}


@router.get("/admin/overview")
def admin_overview():
    return repo_auth.overview()


@router.get("/admin/users")
def admin_users():
    return repo_auth.list_users()


@router.post("/admin/users")
def admin_create_user(payload: AdminCreateUserPayload, request: Request):
    if not repo_auth.valid_email(payload.email):
        raise HTTPException(422, "Escribe un correo válido.")
    if repo_auth.get_user_by_email(payload.email):
        raise HTTPException(409, "Ya existe una cuenta con ese correo.")
    temporary = secrets.token_urlsafe(12) + "A1!"
    try:
        user = repo_auth.create_user(
            name=payload.name,
            email=payload.email,
            password_hash=hash_password(temporary),
            status="activa",
            cargo=payload.cargo,
            must_change_password=True,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    repo_auth.audit(
        event_type="admin_user_created",
        action="create",
        resource="user",
        actor_id=request_user(request)["id"],
        session_id=request.state.session_id,
        actor_name=request_user(request)["nombre"],
        after={"id": user["id"], "email": user["correo"]},
        details={"targetUserId": user["id"]},
    )
    return {"user": user, "temporaryPassword": temporary}


@router.patch("/admin/users/{user_id}")
def admin_update_user(user_id: str, payload: AdminUpdateUserPayload, request: Request):
    actor = request_user(request)
    data = payload.model_dump(exclude_unset=True)
    if user_id == actor["id"] and (data.get("active") is False or data.get("status") == "suspendida"):
        raise HTTPException(400, "No puedes suspender tu propia cuenta.")
    try:
        return repo_auth.update_user(user_id, data)
    except ValueError as exc:
        status = 404 if "no encontrado" in str(exc).lower() else 409
        raise HTTPException(status, str(exc)) from exc


@router.post("/admin/users/{user_id}/approve")
def admin_approve_user(user_id: str, payload: ApproveUserPayload, request: Request):
    actor = request_user(request)
    try:
        user = repo_auth.approve_user(user_id, payload.roleIds, actor["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    repo_auth.audit(
        event_type="admin_user_approved",
        action="approve",
        resource="user",
        actor_id=actor["id"],
        session_id=request.state.session_id,
        actor_name=actor["nombre"],
        details={"targetUserId": user_id},
        after={"status": "activa", "roles": user["roles"]},
    )
    return user


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, request: Request):
    target = repo_auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Usuario no encontrado.")
    temporary = secrets.token_urlsafe(12) + "A1!"
    repo_auth.set_password(user_id, hash_password(temporary), True)
    repo_auth.revoke_user_sessions(user_id, "admin_password_reset")
    actor = request_user(request)
    repo_auth.audit(
        event_type="admin_password_reset",
        action="reset_password",
        resource="user",
        actor_id=actor["id"],
        session_id=request.state.session_id,
        actor_name=actor["nombre"],
        details={"targetUserId": user_id},
    )
    return {"temporaryPassword": temporary, "mustChangePassword": True}


@router.put("/admin/users/{user_id}/access")
def admin_user_access(user_id: str, payload: UserAccessPayload, request: Request):
    actor = request_user(request)
    try:
        return repo_auth.replace_user_access(
            user_id,
            role_ids=payload.roleIds,
            group_ids=payload.groupIds,
            direct_permissions=[item.model_dump() for item in payload.permissions],
            actor_id=actor["id"],
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/admin/users/{user_id}/superadmin")
def admin_set_superadmin(user_id: str, payload: SuperadminPayload, request: Request):
    actor = request_user(request)
    before = repo_auth.get_user_by_id(user_id)
    if not before:
        raise HTTPException(404, "Usuario no encontrado.")
    try:
        user = repo_auth.set_superadmin(user_id, payload.enabled, actor["id"])
    except ValueError as exc:
        status = 404 if "no encontrado" in str(exc).lower() else 409
        raise HTTPException(status, str(exc)) from exc
    repo_auth.audit(
        event_type="admin_superadmin_changed",
        action="grant_superadmin" if payload.enabled else "revoke_superadmin",
        resource="user",
        actor_id=actor["id"],
        session_id=request.state.session_id,
        actor_name=actor["nombre"],
        details={"targetUserId": user_id},
        before={"isSuperadmin": bool(before.get("isSuperadmin"))},
        after={"isSuperadmin": payload.enabled},
    )
    return user


@router.get("/admin/roles")
def admin_roles():
    return repo_auth.list_roles()


@router.post("/admin/roles")
def admin_create_role(payload: RoleCreatePayload, request: Request):
    try:
        return repo_auth.create_role(payload.model_dump(), request_user(request)["id"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/admin/roles/{role_id}")
def admin_update_role(role_id: str, payload: RoleUpdatePayload):
    try:
        return repo_auth.update_role(role_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/admin/roles/{role_id}/permissions")
def admin_role_permissions(role_id: str, payload: RolePermissionsPayload, request: Request):
    try:
        return repo_auth.replace_role_permissions(role_id, payload.permissions, request_user(request)["id"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/admin/groups")
def admin_groups():
    return repo_auth.list_groups()


@router.post("/admin/groups")
def admin_create_group(payload: GroupPayload, request: Request):
    try:
        return repo_auth.create_group(payload.model_dump(), request_user(request)["id"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/admin/groups/{group_id}")
def admin_update_group(group_id: str, payload: GroupUpdatePayload):
    return repo_auth.update_group(group_id, payload.model_dump(exclude_unset=True))


@router.put("/admin/groups/{group_id}/access")
def admin_group_access(group_id: str, payload: GroupAccessPayload, request: Request):
    return repo_auth.replace_group_access(
        group_id,
        {
            "members": payload.members,
            "roles": payload.roles,
            "permissions": [item.model_dump() for item in payload.permissions],
        },
        request_user(request)["id"],
    )


@router.get("/admin/permissions")
def admin_permissions():
    return repo_auth.list_permissions()


@router.get("/admin/audit")
def admin_audit(
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    action: str | None = None,
    userId: str | None = None,
):
    return repo_auth.list_audit(limit=limit, offset=offset, search=search, action=action, user_id=userId)


@router.get("/admin/sessions")
def admin_sessions():
    return repo_auth.list_all_sessions()


@router.delete("/admin/sessions/{session_id}")
def admin_revoke_session(session_id: str):
    repo_auth.revoke_session(session_id, "admin_revoked")
    return {"ok": True}
