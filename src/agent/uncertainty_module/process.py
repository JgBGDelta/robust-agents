"""Señales de proceso (Capa 2) del modulo de incertidumbre.

Implementa las tres subseñales L2 descritas en
`docs/fase2_especificaciones/fase2_1/metodos_incertidumbre.md` 6.2:

- L2.a cycle detection: acciones equivalentes en ventana reciente.
- L2.b observation failure rate: ratio reciente de fallos del entorno.
- L2.c trajectory volatility: reversiones detectadas a partir de la
  historia de `surface_signature` de los `StructuralRiskResult` previos.

Cada subseñal devuelve un valor en `[0, 1]` (mas alto = mas incertidumbre).
Se calculan sobre la traza ya existente, sin llamadas al modelo.
"""

from __future__ import annotations

import re
from typing import Any

_RETURNCODE_RE = re.compile(r"<returncode>(\d+)</returncode>")
_EXCEPTION_RE = re.compile(r"<exception>(.+?)</exception>", re.DOTALL)


class ProcessSignals:
    """Calcula las tres subseñales de proceso (L2.a, L2.b, L2.c)."""

    def __init__(self, history_window: int = 5):
        """Inicializa el tamaño de ventana para señales sobre la traza."""
        self._history_window = max(history_window, 1)

    def cycle(self, message: dict[str, Any], recent_history: list[dict[str, Any]]) -> float:
        """Detecta acciones equivalentes a la actual dentro de la ventana reciente.

        Compara primero las `actions` parseadas (mas robusto que el texto);
        si no estan disponibles, recurre a una firma textual del contenido.
        Devuelve la fraccion de mensajes asistente recientes que coinciden.
        """
        current_key = self._action_key(message)
        if not current_key:
            return 0.0
        # Recoleccion de claves de los mensajes assistant previos en la ventana.
        previous_keys = [
            self._action_key(entry)
            for entry in recent_history
            if entry.get("role") == "assistant"
        ]
        previous_keys = [key for key in previous_keys if key]
        if not previous_keys:
            return 0.0
        matches = sum(1 for key in previous_keys if key == current_key)
        return min(matches / len(previous_keys), 1.0)

    def failure_rate(self, recent_history: list[dict[str, Any]]) -> float:
        """Estima la fraccion de observaciones recientes con fallo del entorno."""
        observations = [entry for entry in recent_history if self._is_observation(entry)]
        if not observations:
            return 0.0
        failures = sum(1 for entry in observations if self._is_failure(entry))
        return failures / len(observations)

    def volatility(self, signals_history: list[dict[str, Any]]) -> float:
        """Estima la volatilidad de la trayectoria sobre los ultimos `surface_signature`.

        Considera el segmento mas reciente de `StructuralRiskResult` y
        cuenta las reversiones (un `surface_signature` que reaparece tras
        haber cambiado). Normaliza por el tamaño de la ventana efectiva.
        """
        signatures = [
            entry.get("structural_risk", {}).get("diff_summary", {}).get("surface_signature")
            for entry in signals_history
            if isinstance(entry, dict) and "structural_risk" in entry
        ]
        signatures = [sig for sig in signatures if sig]
        if len(signatures) < 2:
            return 0.0
        window = signatures[-self._history_window :]
        reverts = 0
        seen: set[str] = set()
        previous: str | None = None
        for sig in window:
            if previous is not None and sig != previous and sig in seen:
                reverts += 1
            seen.add(sig)
            previous = sig
        return min(reverts / max(len(window) - 1, 1), 1.0)

    def _action_key(self, message: dict[str, Any]) -> str:
        """Construye una clave estable para identificar acciones equivalentes."""
        actions = message.get("extra", {}).get("actions")
        if isinstance(actions, list) and actions:
            return "|".join(self._normalize_action(action) for action in actions)
        content = (message.get("content") or "").strip()
        return content[:200].lower() if content else ""

    def _normalize_action(self, action: Any) -> str:
        """Reduce una accion a una cadena comparable (comando shell sin espacios extra)."""
        if isinstance(action, dict):
            command = action.get("command") or action.get("action") or ""
            return " ".join(str(command).split())
        return " ".join(str(action).split())

    def _is_observation(self, entry: dict[str, Any]) -> bool:
        """Identifica mensajes de observacion (resultado de ejecutar acciones).

        Comprueba primero el camino estructurado (extra con claves explícitas);
        si extra está vacío, usa un fallback que busca etiquetas XML en el
        contenido de texto plano generado por JsonBashModel.
        """
        if entry.get("role") != "user":
            return False
        extra = entry.get("extra") or {}
        if any(key in extra for key in ("returncode", "outputs", "exception_info")):
            return True
        # Fallback: formato texto plano de JsonBashModel (<returncode> / <exception>)
        content = entry.get("content") or ""
        return bool(_RETURNCODE_RE.search(content) or _EXCEPTION_RE.search(content))

    def _is_failure(self, entry: dict[str, Any]) -> bool:
        """Determina si una observacion indica fallo del entorno.

        Comprueba primero el camino estructurado; si extra está vacío, parsea
        el returncode y la etiqueta <exception> desde el contenido de texto.
        """
        extra = entry.get("extra") or {}
        returncode = extra.get("returncode")
        if isinstance(returncode, int) and returncode != 0:
            return True
        if extra.get("exception_info"):
            return True
        outputs = extra.get("outputs")
        if isinstance(outputs, list):
            return any(
                isinstance(out, dict)
                and (
                    (isinstance(out.get("returncode"), int) and out["returncode"] != 0)
                    or out.get("exception_info")
                )
                for out in outputs
            )
        # Fallback: parseo desde texto plano de JsonBashModel
        content = entry.get("content") or ""
        rc_match = _RETURNCODE_RE.search(content)
        if rc_match and int(rc_match.group(1)) != 0:
            return True
        exc_match = _EXCEPTION_RE.search(content)
        if exc_match and exc_match.group(1).strip():
            return True
        return False
