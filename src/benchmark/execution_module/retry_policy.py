"""`RetryPolicy` del modulo de ejecucion.

`modulo_ejecucion.md` ME.7. Politica conservadora:

- max 1 reintento (decision D2),
- solo errores transitorios reintentables: timeouts de Docker o de
  red, HTTP 5xx en la API del modelo, excepciones del entorno
  catalogadas como recuperables por `mini-SWE-agent`.
- **No** se reintentan: excepciones del agente (parseo, aplicacion
  del parche, crashes), `precondition_failed`, agotamiento de
  `cost_limit`/`step_limit`, `Submitted` con parche vacio.

La clasificacion es heuristica: agrupamos por jerarquia de
excepciones estandar y por patrones de mensaje (HTTP 5xx). Si la
heuristica falla en algun caso concreto, el run quedara como `failed`
sin reintento, que es la opcion conservadora.
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from typing import Any


_TRANSIENT_BASE_CLASSES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    socket.timeout,
)
"""Excepciones estandar consideradas transitorias por defecto."""

_HTTP_5XX_RE = re.compile(r"\b5\d{2}\b")
"""Coincide con codigos HTTP 5xx en mensajes de error."""

_TRANSIENT_NAME_PATTERNS: tuple[str, ...] = (
    "Timeout",
    "ServerError",
    "ServiceUnavailable",
    "Throttled",
    "DockerException",
)
"""Nombres de clase frecuentes para errores transitorios (heuristica blanda)."""


@dataclass(frozen=True)
class RetryDecision:
    """Resultado de evaluar si un run debe reintentarse."""

    should_retry: bool
    reason: str


class RetryPolicy:
    """Decide si un run fallido es reintentable y cuantos intentos quedan."""

    def __init__(self, *, max_retries: int = 1) -> None:
        """Configura el numero maximo de reintentos (`>=0`)."""
        self.max_retries = max(0, int(max_retries))

    def decide_from_error(
        self,
        *,
        error: dict[str, Any] | None,
        attempts_done: int,
    ) -> RetryDecision:
        """Decide a partir del payload `error` de un `BenchmarkRunRecord`.

        El `RunExecutor` ya clasifica la excepcion en el momento del
        fallo y lo guarda en `error.transient`. Confiamos en ese flag
        para no tener que reconstruir la excepcion a posteriori.
        """
        if error is None:
            return RetryDecision(False, "no_error")
        if attempts_done > self.max_retries:
            return RetryDecision(False, "max_retries_exhausted")
        if not bool(error.get("transient", False)):
            return RetryDecision(False, "non_transient_exception")
        return RetryDecision(True, "transient_exception")

    @staticmethod
    def is_transient(exception: BaseException) -> bool:
        """Heuristica de clasificacion (EB.7.1)."""
        if isinstance(exception, _TRANSIENT_BASE_CLASSES):
            return True
        cls_name = type(exception).__name__
        if any(pattern in cls_name for pattern in _TRANSIENT_NAME_PATTERNS):
            return True
        message = str(exception)
        if _HTTP_5XX_RE.search(message):
            return True
        return False

    @staticmethod
    def describe(exception: BaseException) -> dict[str, Any]:
        """Devuelve un payload serializable con la causa del fallo."""
        return {
            "exception_type": type(exception).__name__,
            "message": str(exception),
            "transient": RetryPolicy.is_transient(exception),
        }
