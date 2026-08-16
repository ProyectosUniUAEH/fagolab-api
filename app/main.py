import asyncio
import os

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .auth import AuthAclMiddleware, SecurityHeadersMiddleware
from .auth_api import router as auth_router
from .chat_api import router as chat_router
from .ia_api import router as ia_router
from .tareas_api import router as tareas_router
from .config import settings
from .db import close_pool, ping
from .realtime import capture_event_loop, handle_presence_socket

app = FastAPI(title="fago-api", version="0.2.0")

app.add_middleware(AuthAclMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # En local, el dev server puede usar cualquier puerto (5173, 5174, 4173...).
    # Local: localhost, 127.0.0.1, IPs 192.168/10/172.16-31 y Tailscale 100
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+|100\.\d+\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(chat_router)
app.include_router(tareas_router)
app.include_router(ia_router)

# Archivos estáticos: cliente de prueba WS + imágenes subidas (media).
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

os.makedirs(settings.MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")

# PDFs de la biblioteca de bibliografía.
from .repo_biblioteca import BIBLIO_DIR  # noqa: E402
os.makedirs(BIBLIO_DIR, exist_ok=True)
app.mount("/biblioteca", StaticFiles(directory=str(BIBLIO_DIR)), name="biblioteca")


@app.on_event("startup")
def _respaldo_al_arrancar():
    """Guarda una copia de seguridad diaria automática (si hoy aún no hay ninguna)."""
    try:
        from . import admin
        admin.respaldo_automatico_diario()
    except Exception:
        pass


@app.on_event("startup")
def _sincronizar_permisos():
    """Materializa el catálogo canónico de permisos después de las migraciones."""
    try:
        from . import repo_auth
        repo_auth.sync_catalog()
    except Exception as exc:
        print(f"[auth] no se pudo sincronizar el catálogo ACL: {exc}")


@app.on_event("startup")
def _bootstrap_token_banner():
    """Imprime el token de primer arranque (Jenkins / instalador) o confirma que ya murió."""
    try:
        from . import repo_bootstrap
        token = repo_bootstrap.ensure_token_on_startup()
        if token:
            setup_url = f"{settings.PUBLIC_APP_URL}/#/configuracion-inicial?token={token}"
            print("*" * 62)
            print("  FagoLab · token de instalación (un solo uso)")
            print(f"  {token}")
            print(f"  {setup_url}")
            print("  Tras crear la primera administradora este token se destruye.")
            print("*" * 62)
        elif repo_bootstrap.is_locked():
            print("[auth] instalación cerrada: el token de primer arranque ya no existe.")
    except Exception as exc:
        print(f"[auth] no se pudo preparar el token de instalación: {exc}")


@app.on_event("startup")
async def _inicializar_realtime():
    """Captura el loop ASGI para publicar desde handlers HTTP síncronos."""
    capture_event_loop(asyncio.get_running_loop())


@app.on_event("shutdown")
def _cerrar_pool_db():
    close_pool()


@app.get("/")
async def root():
    return {"message": "fago-api is running", "service": "fago-api", "env": settings.ENV}


@app.get("/health")
async def health():
    return {"status": "healthy", "db": ping()}


@app.get("/test-ws", response_class=HTMLResponse)
async def test_ws():
    test_file = os.path.join(static_dir, "test-websocket.html")
    if os.path.exists(test_file):
        with open(test_file, "r") as f:
            return f.read()
    return "<h1>WebSocket test client not found</h1>"


@app.websocket("/ws")
@app.websocket("/ws/presence")
async def websocket_endpoint(websocket: WebSocket):
    """Canal autenticado. /ws se conserva como alias para clientes anteriores."""
    await handle_presence_socket(websocket)
