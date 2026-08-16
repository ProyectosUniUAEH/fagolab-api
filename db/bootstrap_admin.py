"""Crea la primera administradora de FagoLab de forma interactiva.

Uso:
  py -3 db/bootstrap_admin.py

La contraseña se solicita sin mostrarla. El script se niega a crear una segunda
superadministradora; las cuentas posteriores se gestionan desde el panel.
"""
from __future__ import annotations

from getpass import getpass
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.auth import hash_password
from app import repo_auth


def main() -> None:
    repo_auth.sync_catalog()
    if any(user["isSuperadmin"] for user in repo_auth.list_users()):
        raise SystemExit("Ya existe una superadministradora. Usa el panel de seguridad.")
    name = input("Nombre de la administradora: ").strip()
    email = input("Correo: ").strip().lower()
    if not name or not repo_auth.valid_email(email):
        raise SystemExit("Nombre o correo inválido.")
    password = getpass("Contraseña (mínimo 12 caracteres): ")
    confirmation = getpass("Repite la contraseña: ")
    if password != confirmation:
        raise SystemExit("Las contraseñas no coinciden.")
    user = repo_auth.create_user(
        name=name,
        email=email,
        password_hash=hash_password(password),
        status="activa",
        is_superadmin=True,
    )
    print(f"Administradora creada: {user['nombre']} <{user['correo']}>")


if __name__ == "__main__":
    main()
