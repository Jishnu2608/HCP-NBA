"""Create the local secrets the application needs, in the git-ignored .env file.

    python -m app.bootstrap

Generates a random value for each required secret that is not already set. Existing
values are never changed. Nothing is printed except which names were created: open .env
to read them.
"""

import secrets
import sys

from app.core.config import ENV_FILE, get_settings

# name -> generator. Passwords include a letter and a digit and avoid ambiguous symbols.
REQUIRED = {
    "NBA_JWT_SECRET": lambda: secrets.token_urlsafe(48),
    "NBA_ADMIN_PASSWORD": lambda: f"Nba-{secrets.token_urlsafe(9)}7",
    "NBA_DEMO_PASSWORD": lambda: f"Demo-{secrets.token_urlsafe(6)}7",
}


def existing_keys() -> set[str]:
    if not ENV_FILE.exists():
        return set()
    keys = set()
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def ensure_secrets() -> list[str]:
    """Appends any missing secret to .env. Returns the names that were created."""
    present = existing_keys()
    missing = [name for name in REQUIRED if name not in present]
    if missing:
        text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        if not text:
            text = "# Local secrets. This file is git-ignored: never commit it.\n"
        text += "".join(f"{name}={REQUIRED[name]()}\n" for name in missing)
        ENV_FILE.write_text(text, encoding="utf-8")
        get_settings.cache_clear()
    return missing


def main() -> int:
    created = ensure_secrets()
    if created:
        print(f"Created in {ENV_FILE}: {', '.join(created)}")
        print("Open that file to read the values. Reseed (python -m app.datagen) to apply")
        print("new account passwords to the database.")
    else:
        print(f"All required secrets are already set in {ENV_FILE}. Nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
