"""Coordinacion de parada del benchmark (Ctrl+C / quota agotada).

Un unico evento compartido entre el hilo principal, workers paralelos y
callbacks de LiteLLM. Cuando se activa, no se encolan nuevos runs y los bucles
de ejecucion abortan en cuanto pueden.
"""

from __future__ import annotations

import threading

_stop_event = threading.Event()


def clear_stop() -> None:
    """Reinicia el flag al inicio de un experimento."""
    _stop_event.clear()


def request_stop() -> None:
    """Marca que el experimento debe detenerse."""
    _stop_event.set()


def stop_requested() -> bool:
    """True si se pidio detener el experimento."""
    return _stop_event.is_set()


def force_process_exit(code: int = 130) -> None:
    """Termina el proceso de inmediato (Ctrl+C).

    ``sys.exit`` puede quedarse bloqueado si quedan hilos worker no-daemon
    ejecutando agentes/Docker. ``os._exit`` evita ese bloqueo.
    """
    import os

    os._exit(code)
