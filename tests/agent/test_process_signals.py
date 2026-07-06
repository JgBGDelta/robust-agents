"""Tests de regresion para ProcessSignals con el formato real de JsonBashModel.

Verifica que _is_observation y _is_failure funcionan correctamente cuando extra
esta vacio y los datos de returncode/exception vienen en el contenido de texto
plano generado por JsonBashModel (formato <returncode>N</returncode>).
"""

from __future__ import annotations

import pytest

from agent.uncertainty_module.process import ProcessSignals


def _obs(content: str) -> dict:
    """Mensaje user con extra vacio, como genera JsonBashModel."""
    return {"role": "user", "content": content, "extra": {}}


def _obs_structured(returncode: int, exception_info: str = "") -> dict:
    """Mensaje user con extra estructurado (camino clasico)."""
    extra = {"returncode": returncode}
    if exception_info:
        extra["exception_info"] = exception_info
    return {"role": "user", "content": "output", "extra": extra}


@pytest.fixture()
def signals() -> ProcessSignals:
    """Fixture de pytest: signals."""
    return ProcessSignals()


# ---------------------------------------------------------------------------
# _is_observation


def test_is_observation_detects_returncode_tag(signals: ProcessSignals):
    """Comprueba que is observation detects returncode tag."""
    msg = _obs("<returncode>0</returncode>\n<output>ok</output>")
    assert signals._is_observation(msg) is True


def test_is_observation_detects_exception_tag(signals: ProcessSignals):
    """Comprueba que is observation detects exception tag."""
    msg = _obs("<exception>Traceback ...</exception>")
    assert signals._is_observation(msg) is True


def test_is_observation_ignores_assistant_messages(signals: ProcessSignals):
    """Comprueba que is observation ignores assistant messages."""
    msg = {"role": "assistant", "content": "<returncode>0</returncode>", "extra": {}}
    assert signals._is_observation(msg) is False


def test_is_observation_still_works_with_structured_extra(signals: ProcessSignals):
    """Comprueba que is observation still works with structured extra."""
    assert signals._is_observation(_obs_structured(returncode=0)) is True


# ---------------------------------------------------------------------------
# _is_failure


def test_is_failure_nonzero_returncode_in_content(signals: ProcessSignals):
    """Comprueba que is failure nonzero returncode in content."""
    msg = _obs("<returncode>1</returncode>\n<output>error</output>")
    assert signals._is_failure(msg) is True


def test_is_failure_zero_returncode_not_failure(signals: ProcessSignals):
    """Comprueba que is failure zero returncode not failure."""
    msg = _obs("<returncode>0</returncode>\n<output>ok</output>")
    assert signals._is_failure(msg) is False


def test_is_failure_exception_tag_nonempty(signals: ProcessSignals):
    """Comprueba que is failure exception tag nonempty."""
    msg = _obs("<exception>Traceback (most recent call last):\n  File ...</exception>")
    assert signals._is_failure(msg) is True


def test_is_failure_empty_exception_tag_not_failure(signals: ProcessSignals):
    """Comprueba que is failure empty exception tag not failure."""
    msg = _obs("<exception></exception>\n<returncode>0</returncode>")
    assert signals._is_failure(msg) is False


def test_is_failure_structured_extra_still_works(signals: ProcessSignals):
    """Comprueba que is failure structured extra still works."""
    assert signals._is_failure(_obs_structured(returncode=1)) is True
    assert signals._is_failure(_obs_structured(returncode=0)) is False


# ---------------------------------------------------------------------------
# failure_rate con formato real de JsonBashModel


def test_failure_rate_real_jsonbashmodel_format(signals: ProcessSignals):
    """Regresion: failure_rate debe dar >0 con el formato de texto plano real."""
    history = [
        _obs("<returncode>0</returncode>\n<output>ok</output>"),
        _obs("<returncode>1</returncode>\n<output>error</output>"),
        _obs("<exception>Traceback...</exception>\n<returncode>1</returncode>\n<output></output>"),
        {"role": "assistant", "content": '{"command": "ls"}', "extra": {}},
    ]
    rate = signals.failure_rate(history)
    assert rate == pytest.approx(2 / 3)


def test_failure_rate_all_successful_observations(signals: ProcessSignals):
    """Comprueba que failure rate all successful observations."""
    history = [
        _obs("<returncode>0</returncode>\n<output>ok</output>"),
        _obs("<returncode>0</returncode>\n<output>also ok</output>"),
    ]
    assert signals.failure_rate(history) == pytest.approx(0.0)


def test_failure_rate_empty_history(signals: ProcessSignals):
    """Comprueba que failure rate empty history."""
    assert signals.failure_rate([]) == pytest.approx(0.0)
