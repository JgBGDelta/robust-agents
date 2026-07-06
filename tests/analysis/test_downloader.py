"""Tests unitarios del ``Downloader``.

Cubre: cache idempotente, descarga directa (JSONL), descarga+extraccion
de ZIP, backoff con reintentos, fallo tras agotar reintentos, verificacion
de tamano, y extraccion con archive_member explicito.

Sin red real: las descargas se mockean con ``unittest.mock.patch``.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from analysis.external_metrics_module.downloader import Downloader, _is_zip
from analysis.external_metrics_module.external_metrics_config import ExternalMetricsConfig
from analysis.external_metrics_module.external_source_config import ExternalSourceConfig


# ---------------------------------------------------------------------------
# Fabricas auxiliares
# ---------------------------------------------------------------------------


def _config(max_retries: int = 3, max_bytes: int = 500 * 1024 * 1024) -> ExternalMetricsConfig:
    """Auxiliar interno: config."""
    return ExternalMetricsConfig(max_retries=max_retries, max_download_bytes=max_bytes)


def _source(tmp_path: Path, *, archive_member: str | None = None) -> ExternalSourceConfig:
    """Auxiliar interno: source."""
    return ExternalSourceConfig(
        source_id="test_source",
        release_url="https://example.com/preds.jsonl",
        archive_member=archive_member,
        local_cache_dir=tmp_path / "cache" / "test_source",
        default_agent_id="test_source",
    )


def _make_zip(filename: str, content: bytes) -> bytes:
    """Crea un ZIP en memoria con un unico fichero."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


_SAMPLE_JSONL = b'{"instance_id": "x__x-1", "model_patch": "diff", "model_name_or_path": "m"}\n'


# ---------------------------------------------------------------------------
# _is_zip
# ---------------------------------------------------------------------------


def test_is_zip_true_for_zip_magic():
    """Comprueba que is zip true for zip magic."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "hello")
    assert _is_zip(buf.getvalue()) is True


def test_is_zip_false_for_plain_text():
    """Comprueba que is zip false for plain text."""
    assert _is_zip(b"Hello world") is False


def test_is_zip_false_for_short_data():
    """Comprueba que is zip false for short data."""
    assert _is_zip(b"PK") is False


# ---------------------------------------------------------------------------
# Cache idempotente: no vuelve a descargar si el JSONL ya existe
# ---------------------------------------------------------------------------


def test_ensure_jsonl_uses_cache_when_valid(tmp_path: Path):
    """Comprueba que ensure jsonl uses cache when valid."""
    source = _source(tmp_path)
    source.local_cache_dir.mkdir(parents=True)
    cached = source.local_cache_dir / "test_source.jsonl"
    cached.write_bytes(_SAMPLE_JSONL)

    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch") as mock_fetch:
        result = downloader.ensure_jsonl(source)

    mock_fetch.assert_not_called()
    assert result == cached


def test_ensure_jsonl_redownloads_when_file_empty(tmp_path: Path):
    """Comprueba que ensure jsonl redownloads when file empty."""
    source = _source(tmp_path)
    source.local_cache_dir.mkdir(parents=True)
    # Fichero vacio: no se considera valido.
    (source.local_cache_dir / "test_source.jsonl").write_bytes(b"")

    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch", return_value=_SAMPLE_JSONL):
        result = downloader.ensure_jsonl(source)

    assert result.stat().st_size > 0


# ---------------------------------------------------------------------------
# Descarga directa de JSONL (sin ZIP)
# ---------------------------------------------------------------------------


def test_ensure_jsonl_direct_jsonl(tmp_path: Path):
    """Comprueba que ensure jsonl direct jsonl."""
    source = _source(tmp_path)
    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch", return_value=_SAMPLE_JSONL):
        result = downloader.ensure_jsonl(source)

    assert result.exists()
    assert result.read_bytes() == _SAMPLE_JSONL


# ---------------------------------------------------------------------------
# Descarga de ZIP con archive_member automatico
# ---------------------------------------------------------------------------


def test_ensure_jsonl_extracts_jsonl_from_zip_auto(tmp_path: Path):
    """Comprueba que ensure jsonl extracts jsonl from zip auto."""
    zip_bytes = _make_zip("predictions.jsonl", _SAMPLE_JSONL)
    source = _source(tmp_path, archive_member=None)
    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch", return_value=zip_bytes):
        result = downloader.ensure_jsonl(source)

    assert result.read_bytes() == _SAMPLE_JSONL


def test_ensure_jsonl_extracts_jsonl_from_zip_explicit_member(tmp_path: Path):
    """Comprueba que ensure jsonl extracts jsonl from zip explicit member."""
    zip_bytes = _make_zip("preds.jsonl", _SAMPLE_JSONL)
    source = _source(tmp_path, archive_member="preds.jsonl")
    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch", return_value=zip_bytes):
        result = downloader.ensure_jsonl(source)

    assert result.read_bytes() == _SAMPLE_JSONL


def test_ensure_jsonl_raises_if_archive_member_not_found(tmp_path: Path):
    """Comprueba que ensure jsonl raises if archive member not found."""
    zip_bytes = _make_zip("other.jsonl", _SAMPLE_JSONL)
    source = _source(tmp_path, archive_member="missing.jsonl")
    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch", return_value=zip_bytes):
        with pytest.raises(ValueError, match="missing.jsonl"):
            downloader.ensure_jsonl(source)


def test_ensure_jsonl_raises_if_zip_has_no_jsonl(tmp_path: Path):
    """Comprueba que ensure jsonl raises if zip has no jsonl."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "nada aqui")
    zip_bytes = buf.getvalue()
    source = _source(tmp_path, archive_member=None)
    downloader = Downloader(_config())
    with patch.object(downloader, "_fetch", return_value=zip_bytes):
        with pytest.raises(ValueError, match="jsonl"):
            downloader.ensure_jsonl(source)


# ---------------------------------------------------------------------------
# Reintentos y backoff
# ---------------------------------------------------------------------------


def test_ensure_jsonl_retries_on_failure(tmp_path: Path):
    """Falla 2 veces y tiene exito en el tercer intento."""
    source = _source(tmp_path)
    downloader = Downloader(_config(max_retries=3))
    call_count = 0

    def _flaky_fetch(url: str) -> bytes:
        """Auxiliar interno: flaky fetch."""
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise OSError("timeout simulado")
        return _SAMPLE_JSONL

    with patch.object(downloader, "_fetch", side_effect=_flaky_fetch):
        with patch("analysis.external_metrics_module.downloader.time.sleep"):
            result = downloader.ensure_jsonl(source)

    assert call_count == 3
    assert result.exists()


def test_ensure_jsonl_raises_after_all_retries_exhausted(tmp_path: Path):
    """Comprueba que ensure jsonl raises after all retries exhausted."""
    source = _source(tmp_path)
    downloader = Downloader(_config(max_retries=2))

    with patch.object(downloader, "_fetch", side_effect=OSError("fallo permanente")):
        with patch("analysis.external_metrics_module.downloader.time.sleep"):
            with pytest.raises(RuntimeError, match="Descarga fallida"):
                downloader.ensure_jsonl(source)


# ---------------------------------------------------------------------------
# Verificacion de tamano
# ---------------------------------------------------------------------------


def test_ensure_jsonl_raises_if_response_empty(tmp_path: Path):
    """Comprueba que ensure jsonl raises if response empty."""
    source = _source(tmp_path)
    downloader = Downloader(_config(max_retries=1))
    with patch.object(downloader, "_fetch", return_value=b""):
        with pytest.raises(RuntimeError, match="Descarga fallida"):
            downloader.ensure_jsonl(source)


def test_ensure_jsonl_raises_if_response_too_large(tmp_path: Path):
    """Comprueba que ensure jsonl raises if response too large."""
    source = _source(tmp_path)
    downloader = Downloader(_config(max_retries=1, max_bytes=10))
    with patch.object(downloader, "_fetch", return_value=b"x" * 11):
        with pytest.raises(RuntimeError, match="Descarga fallida"):
            downloader.ensure_jsonl(source)
