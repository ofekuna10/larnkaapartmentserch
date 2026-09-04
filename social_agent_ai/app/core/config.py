"""Application settings, loaded once from the environment.

Nothing outside this module should read ``os.environ`` directly; import
``get_settings()`` instead so tests can override the whole object.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    ECHO = "echo"  # deterministic offline stub, used by the test suite


class VectorBackend(str, Enum):
    QDRANT = "qdrant"
    MEMORY = "memory"


class Settings(BaseSettings):
    """Every knob the service exposes. See ``.env.example`` for defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application --------------------------------------------------------
    app_name: str = "SocialAgent AI"
    app_env: AppEnv = AppEnv.DEVELOPMENT
    debug: bool = True
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Security -----------------------------------------------------------
    secret_key: str = "dev-only-insecure-secret"
    access_token_ttl_minutes: int = 60
    token_encryption_key: Optional[str] = None

    # --- LLM ----------------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-5"
    # Current Claude models reject `budget_tokens`; adaptive thinking replaces it.
    llm_adaptive_thinking: bool = True
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    llm_max_tokens: int = 16000
    # Anthropic calls send no sampling parameters (current models reject them);
    # this applies to the OpenAI adapter only.
    llm_temperature: float = 0.4
    llm_timeout_seconds: float = 90.0

    # --- Persistence --------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/social_agent"
    )
    database_pool_size: int = 10
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_anon_key: Optional[str] = None

    # --- Vector store -------------------------------------------------------
    vector_backend: VectorBackend = VectorBackend.MEMORY
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "brand_voice"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # --- Meta Graph API -----------------------------------------------------
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    meta_graph_version: str = "v20.0"
    meta_redirect_uri: Optional[str] = None
    meta_webhook_verify_token: Optional[str] = None

    # --- YouTube Data API v3 ------------------------------------------------
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None
    youtube_api_key: Optional[str] = None
    youtube_redirect_uri: Optional[str] = None

    # --- TikTok -------------------------------------------------------------
    tiktok_client_key: Optional[str] = None
    tiktok_client_secret: Optional[str] = None
    tiktok_redirect_uri: Optional[str] = None

    # --- Pipeline behaviour -------------------------------------------------
    max_validation_retries: int = Field(default=2, ge=0, le=5)
    auto_publish_enabled: bool = False
    min_brand_voice_score: float = Field(default=0.75, ge=0.0, le=1.0)
    min_safety_score: float = Field(default=0.90, ge=0.0, le=1.0)
    analytics_lookback_days: int = Field(default=90, ge=1, le=365)
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept ``a,b,c`` from the environment as well as a JSON list."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                return value
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        """Refuse to boot production with development placeholders."""
        if self.app_env is not AppEnv.PRODUCTION:
            return self
        problems: list[str] = []
        if self.secret_key == "dev-only-insecure-secret":
            problems.append("SECRET_KEY is still the development default")
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if not self.token_encryption_key:
            problems.append("TOKEN_ENCRYPTION_KEY is required")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def meta_graph_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.meta_graph_version}"

    def require(self, *names: str) -> None:
        """Fail fast when a code path needs credentials that were not supplied."""
        missing = [name for name in names if not getattr(self, name, None)]
        if missing:
            raise RuntimeError(
                "Missing required configuration: " + ", ".join(sorted(missing))
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (cached; call ``.cache_clear()`` in tests)."""
    return Settings()
