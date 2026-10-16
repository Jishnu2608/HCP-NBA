from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
ENV_FILE = REPO_ROOT / ".env"
# The two local databases, chosen explicitly with NBA_DATABASE_PROFILE (never switched or
# created silently): the original one, and its copy where AI tables and AI work live.
DATABASE_FILES = {
    "traditional": DATA_DIR / "nba_demo.db",
    "ai": DATA_DIR / "nba_demodb_ai.db",
}


class MissingSecret(RuntimeError):
    def __init__(self, env_name: str) -> None:
        super().__init__(
            f"{env_name} is not set. Run `python -m app.bootstrap` from the backend folder to "
            f"create it in {ENV_FILE.name}, or set it in the environment."
        )


class Settings(BaseSettings):
    """Runtime configuration. Override any field with an NBA_-prefixed env var or .env entry.

    No secret has a default in code. Secrets live only in the environment or in the
    git-ignored .env file, which `python -m app.bootstrap` creates with random values.
    """

    model_config = SettingsConfigDict(env_prefix="NBA_", env_file=ENV_FILE, extra="ignore")

    app_name: str = "Healthcare NBA"
    environment: str = "local"

    # Which local database: "traditional" (default, data/nba_demo.db) or "ai"
    # (data/nba_demodb_ai.db, the copy that holds the AI tables). Or set database_url to any
    # SQLAlchemy URL (Postgres) instead; setting both is refused.
    database_profile: str = ""
    database_url: str = ""

    # --- Secrets: no defaults. Read them through `secret()`. ---
    jwt_secret: str | None = None
    # Password of the fixed administrator account (created by the generator and at start-up).
    admin_password: str | None = None
    # Shared password of the seeded demo accounts (cm01@nba.demo, rep01@nba.demo, ...).
    demo_password: str | None = None

    jwt_algorithm: str = "HS256"
    # Sessions: absolute lifetime and idle timeout (server-side, see auth/sessions.py).
    access_token_minutes: int = 480
    session_idle_minutes: int = 60

    # Origins allowed to send state-changing requests besides the request's own host
    # (comma-separated, e.g. "http://localhost:5173" for the Vite dev server).
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Off only in tests; limits are per process.
    rate_limit_enabled: bool = True
    # Background jobs (app/jobs.py, app/worker.py). "external": run `python -m app.worker`
    # beside the web server. "embedded": the web process hosts one worker thread (fine for a
    # single local process). "off": nothing runs jobs. Empty = embedded in a local run,
    # external otherwise.
    worker_mode: str = ""
    # How often a worker looks for queued jobs and for the scheduled daily cycle.
    worker_poll_seconds: float = 3.0

    # "template" needs no key and no network; other providers are plugged in later.
    llm_provider: str = "template"

    # Shared AI layer (app/ai). See docs/ai-architecture.md.
    # "ollama" = local model on this machine (default), "off" = no generation. "cloud" is
    # refused in this phase: no cloud adapter exists and none is selected, so nothing is ever
    # sent to a cloud API. Selected here on the server only; no automatic fallback between
    # providers.
    ai_provider: str = "ollama"
    ai_ollama_base_url: str = "http://127.0.0.1:11434"
    ai_ollama_model: str = "qwen3.5:9b-q4_K_M"
    ai_timeout_seconds: float = 180.0
    # Case Brief: "template" (default, no model call), "shadow" (the model runs, is checked
    # and audited, readers still see the template) or "prototype" (readers see model wording
    # that passed every check, marked as AI-generated and needing review).
    ai_case_brief_mode: str = "template"
    # At most this many AI generations queued or running at once (one local model).
    ai_max_active_jobs: int = 2
    # Generations kept per subject and operation (older rows are deleted; audit rows stay).
    ai_keep_generations: int = 5
    ai_cloud_vendor: str = ""  # e.g. anthropic, azure_openai, bedrock, vertex
    ai_cloud_model: str = ""
    ai_cloud_base_url: str = ""  # only for vendors that need one
    # NAME of the environment variable holding the key, never the key itself.
    ai_cloud_api_key_env: str = ""

    datagen_seed: int = 20260101

    admin_email: str = "admin@admin.com"
    # Set to false to seed the administrator only.
    seed_demo_accounts: bool = True
    demo_email_domain: str = "nba.demo"

    # Demo mode allows the on-screen one-time code when no mail server is configured.
    demo_mode: bool = True

    # One-time codes are emailed when these are set (use an app password, never commit it).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 30
    verification_token_minutes: int = 30

    # Invitations for professional roles.
    invitation_ttl_hours: int = 72
    # Base of links in emails (set to a LAN address to open them on a phone).
    public_base_url: str = "http://localhost:8000"
    # Shown in emails as the contact for questions; falls back to the sender address.
    support_email: str | None = None

    # Default age of adulthood. Jurisdictions with a different age override it in
    # core/jurisdiction.py. Invited professionals must be adults where they live; patients
    # below it may register and are flagged.
    minor_age: int = 18

    # --- Legal documents: facts only the operator can supply. Unset values appear in the
    # Privacy Policy and Terms as "[To be confirmed]" and keep the "draft" banner on. ---
    legal_controller_name: str | None = None
    legal_controller_address: str | None = None
    legal_privacy_email: str | None = None
    legal_dpo_contact: str | None = None
    legal_eu_representative: str | None = None
    legal_governing_law: str | None = None
    legal_hosting_region: str | None = None
    # Days within which privacy requests are answered (to be confirmed by counsel).
    privacy_response_days: int | None = None

    # Drafting providers that call an external service never receive identifiable patient
    # or HCP data unless the operator turns this on (for example after a data-processing
    # agreement is in place).
    allow_external_identifiable_data: bool = False

    # What a newly verified account is given from the synthetic pool.
    signup_panel_patients: int = 30
    signup_panel_hcps: int = 15

    @model_validator(mode="after")
    def _resolve_database(self) -> "Settings":
        """One database, chosen explicitly. Nothing here falls back to another one."""
        profile = self.database_profile.strip().lower()
        if profile and self.database_url:
            raise ValueError("Set NBA_DATABASE_PROFILE or NBA_DATABASE_URL, not both.")
        if self.database_url:
            return self
        profile = profile or "traditional"
        if profile not in DATABASE_FILES:
            raise ValueError(
                f"NBA_DATABASE_PROFILE must be one of {sorted(DATABASE_FILES)}, not {profile!r}."
            )
        self.database_profile = profile
        self.database_url = f"sqlite:///{DATABASE_FILES[profile].as_posix()}"
        return self

    @property
    def database_label(self) -> str:
        """ "traditional", "ai", or "custom" for an explicit NBA_DATABASE_URL."""
        return self.database_profile or "custom"

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def cookie_secure(self) -> bool:
        """Cookies are Secure everywhere except a local run over plain http."""
        return not self.is_local

    @property
    def embedded_worker(self) -> bool:
        mode = self.worker_mode or ("embedded" if self.is_local else "external")
        return mode == "embedded"

    @property
    def show_development_codes(self) -> bool:
        """An on-screen one-time code is allowed only for a local run without a mail server."""
        return self.demo_mode and self.is_local and not self.smtp_host

    def secret(self, name: str) -> str:
        """A required secret, or a clear error naming the variable that is missing."""
        value = getattr(self, name)
        if not value:
            raise MissingSecret(f"NBA_{name.upper()}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
