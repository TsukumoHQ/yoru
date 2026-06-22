#!/usr/bin/env python
"""Interactive first-run onboarding — the CLI half of the setup flow.

    make setup        # from the repo root

Shares its entire implementation with the web wizard (``/api/v1/setup/*``) via
:class:`SetupService`, so the two can never drift. Use this for headless / CI /
SSH installs where there's no browser.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

# backend/ is the import root (mirrors how uvicorn runs `apps.api.main:app`).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))


def _load_env() -> None:
    """Load backend/.env into os.environ without a hard dotenv dependency."""
    import os

    env_path = _BACKEND_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or (default or "")


def main() -> int:
    _load_env()
    from apps.api.api.services.setup.setup_service import SetupError, SetupService

    service = SetupService()
    status = service.status()

    print("\n  Yoru / overnight-saas — first-run setup\n  " + "-" * 38)
    print(f"  auth provider : {status['auth_provider']}")
    print(f"  database      : {status['database_url']}")

    if status["auth_provider"] != "local":
        print("\n  AUTH_PROVIDER is not 'local' — identity is managed by Supabase.")
        print("  Nothing to set up here. Configure your Supabase project instead.\n")
        return 0

    if status["installed"]:
        print("\n  ✓ This instance is already set up. Sign in at the dashboard.\n")
        return 0

    print("\n  No admin account yet. Let's create one.\n")

    # ── database ──────────────────────────────────────────────────────────
    print("  Database — press Enter to keep the bundled SQLite, or paste a")
    print("  connection URL to use an existing Postgres (postgres://… ).")
    db_url = _prompt("  Database URL", default="").strip()
    if db_url:
        result = service.test_database(db_url)
        if not result.get("ok"):
            print(f"\n  ✗ Could not connect: {result.get('error')}\n")
            return 1
        print(f"  ✓ Connected to {result['url']}")
    else:
        db_url = None  # keep current

    # ── admin ─────────────────────────────────────────────────────────────
    email = _prompt("\n  Admin email")
    first_name = _prompt("  First name (optional)", default="") or None
    while True:
        pw = getpass.getpass("  Admin password (min 8 chars): ")
        pw2 = getpass.getpass("  Confirm password: ")
        if pw != pw2:
            print("  Passwords don't match, try again.")
            continue
        if len(pw) < 8:
            print("  Too short (min 8 chars), try again.")
            continue
        break

    # ── go ────────────────────────────────────────────────────────────────
    try:
        result = service.initialize(
            admin_email=email,
            admin_password=pw,
            first_name=first_name,
            database_url=db_url,
            setup_token=None,
        )
    except SetupError as e:
        print(f"\n  ✗ Setup failed: {e}\n")
        return 1

    print(f"\n  ✓ Admin created: {result['admin_email']}")
    print("  ✓ Config written to backend/.env")
    if result["restart_required"]:
        print("\n  ⚠ You switched databases — restart the API for it to take effect:")
        print("      make down && make dev   (or restart your uvicorn process)\n")
    else:
        print("\n  Done. Start the stack and sign in:  make dev\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
