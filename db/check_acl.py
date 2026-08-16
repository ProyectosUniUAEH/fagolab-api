"""Falla si un endpoint API no tiene regla ACL o si una regla usa permiso inexistente."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.auth_permissions import ACL_RULES, PERMISSIONS, PUBLIC_PATHS, find_acl_rule
from app.main import app


UUID_SAMPLE = "00000000-0000-0000-0000-000000000001"


def sample_path(path: str) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1).lower()
        return UUID_SAMPLE if name.startswith("id_") or name.endswith("_id") else "sample"

    return re.sub(r"\{([^}:]+)(?::[^}]+)?\}", replace, path)


def main() -> None:
    permission_keys = {item.key for item in PERMISSIONS}
    errors: list[str] = []
    for rule in ACL_RULES:
        if rule.permission and rule.permission not in permission_keys:
            errors.append(f"Regla {rule.method} {rule.pattern}: permiso inexistente {rule.permission}")

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = getattr(route, "methods", set()) or set()
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            concrete = sample_path(path)
            if (method, concrete) in PUBLIC_PATHS:
                continue
            if not find_acl_rule(method, concrete):
                errors.append(f"Endpoint sin ACL: {method} {path} (muestra {concrete})")

    if errors:
        print("\n".join(errors))
        raise SystemExit(1)
    print(f"ACL completa: {len(permission_keys)} permisos, {len(ACL_RULES)} reglas.")


if __name__ == "__main__":
    main()
