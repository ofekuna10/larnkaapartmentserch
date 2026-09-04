"""Settings parsing and the production safety checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import AppEnv, LLMProvider, Settings


def _settings(**overrides) -> Settings:
    base = {
        "app_env": AppEnv.PRODUCTION,
        "secret_key": "x" * 40,
        "token_encryption_key": "3TG0nOZKQ9YfQ3kY6mQ0Xh3xN8Jt5nQhFbW1cP2sK4o=",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_cors_origins_accepts_a_comma_separated_string():
    settings = Settings(cors_origins="http://a.test, http://b.test", _env_file=None)
    assert settings.cors_origins == ["http://a.test", "http://b.test"]

    assert Settings(cors_origins="", _env_file=None).cors_origins == []


def test_production_rejects_the_default_secret():
    with pytest.raises(ValidationError, match="development default"):
        _settings(secret_key="dev-only-insecure-secret")


def test_production_rejects_a_short_secret():
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _settings(secret_key="tooshort")


def test_production_requires_a_token_encryption_key():
    with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEY"):
        _settings(token_encryption_key=None)


def test_production_settings_are_accepted_when_complete():
    settings = _settings()
    assert settings.is_production is True


def test_development_tolerates_placeholders():
    """Outside production, an incomplete configuration must still boot."""
    settings = Settings(
        app_env=AppEnv.DEVELOPMENT,
        secret_key="short",
        token_encryption_key=None,
        _env_file=None,
    )
    assert settings.is_production is False


def test_require_names_every_missing_key():
    settings = Settings(app_env=AppEnv.DEVELOPMENT, _env_file=None)
    with pytest.raises(RuntimeError, match="meta_app_id, meta_app_secret"):
        settings.require("meta_app_id", "meta_app_secret")


def test_meta_graph_base_url_tracks_the_version():
    settings = Settings(meta_graph_version="v21.0", _env_file=None)
    assert settings.meta_graph_base_url.endswith("/v21.0")


def test_declared_defaults_target_claude():
    """Asserted on the field defaults, since the environment overrides them."""
    fields = Settings.model_fields
    assert fields["llm_provider"].default is LLMProvider.ANTHROPIC
    assert fields["anthropic_model"].default == "claude-opus-5"
    assert fields["max_validation_retries"].default == 2
    assert fields["auto_publish_enabled"].default is False
