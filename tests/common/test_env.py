"""Tests del loader central de entorno."""

from __future__ import annotations

import os

import pytest

from common.env import api_key_error_for_model, load_project_env, project_root


def test_project_root_points_at_repo():
    """Comprueba que project root points at repo."""
    assert (project_root() / "pyproject.toml").is_file()


def test_load_project_env_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    """Comprueba que load project env is idempotent."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    path1 = load_project_env()
    path2 = load_project_env()
    assert path1 == path2 == project_root() / ".env"


def test_api_key_error_for_gemini_when_missing(monkeypatch: pytest.MonkeyPatch):
    """Comprueba que api key error for gemini when missing."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    err = api_key_error_for_model("gemini/gemini-2.0-flash")
    assert err is not None
    assert "GOOGLE_API_KEY" in err or "GEMINI_API_KEY" in err


def test_api_key_error_none_when_env_set(monkeypatch: pytest.MonkeyPatch):
    """Comprueba que api key error none when env set."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    assert api_key_error_for_model("gemini/gemini-2.0-flash") is None


def test_api_key_env_override(monkeypatch: pytest.MonkeyPatch):
    """Comprueba que api key env override."""
    monkeypatch.delenv("CUSTOM_KEY", raising=False)
    err = api_key_error_for_model("gemini/x", api_key_env="CUSTOM_KEY")
    assert err is not None and "CUSTOM_KEY" in err
    monkeypatch.setenv("CUSTOM_KEY", "x")
    assert api_key_error_for_model("gemini/x", api_key_env="CUSTOM_KEY") is None
