"""No secret has a default in code; missing ones fail loudly; bootstrap creates them."""

import re
import subprocess
from pathlib import Path

import pytest

from app import bootstrap
from app.core.config import MissingSecret, Settings

REPO = Path(__file__).resolve().parents[2]
SECRET_FIELDS = ("jwt_secret", "admin_password", "demo_password")


def test_secrets_have_no_default_in_code(monkeypatch):
    for field in SECRET_FIELDS:
        monkeypatch.delenv(f"NBA_{field.upper()}")
    settings = Settings(_env_file=None)
    for field in SECRET_FIELDS:
        assert getattr(settings, field) is None
        with pytest.raises(MissingSecret, match=f"NBA_{field.upper()}"):
            settings.secret(field)


def test_bootstrap_creates_missing_secrets_and_keeps_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NBA_ADMIN_PASSWORD=chosen-by-the-user\nNBA_SMTP_HOST=smtp.example.org\n")
    monkeypatch.setattr(bootstrap, "ENV_FILE", env)
    assert bootstrap.ensure_secrets() == ["NBA_JWT_SECRET", "NBA_DEMO_PASSWORD"]
    values = dict(line.split("=", 1) for line in env.read_text().splitlines() if "=" in line)
    assert values["NBA_ADMIN_PASSWORD"] == "chosen-by-the-user"
    assert values["NBA_SMTP_HOST"] == "smtp.example.org"
    assert len(values["NBA_JWT_SECRET"]) >= 48 and len(values["NBA_DEMO_PASSWORD"]) >= 10
    before = env.read_text()
    assert bootstrap.ensure_secrets() == [] and env.read_text() == before


def test_env_file_is_ignored_by_git():
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=REPO, capture_output=True, check=False
    )
    assert result.returncode == 0, ".env must be git-ignored"


def test_no_credential_literal_in_tracked_files():
    """A password, secret, key or token assigned as a string literal must not come back."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    name = r"\b\w*(password|passwd|secret|api_key|apikey|token)\w*"
    literal = r"""["'][^"'\s<>{}$]{6,}["']"""
    assignment = re.compile(rf"(?i){name}\s*(:\s*[\w\[\] |]+)?\s*[:=]\s*{literal}")
    # Names that contain one of the words above but hold no secret.
    harmless = ("password_hash", "token_type", "tokenUrl", "TOKEN_KEY", "CHALLENGE_KEY")
    checked = {".py", ".ts", ".tsx", ".yml", ".yaml", ".toml", ".ps1", ".json", ".example"}
    offenders = []
    for item in tracked:
        path = REPO / item
        if path.suffix not in checked or path.name in ("package-lock.json", "test_secrets.py"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if assignment.search(line) and not any(word in line for word in harmless):
                offenders.append(f"{item}:{number}: {line.strip()}")
    assert offenders == []
