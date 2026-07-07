"""Señales single-shot del modelo (Capa 1).

Implementa las tres subseñales L1 descritas en
`docs/fase2_especificaciones/fase2_1/metodos_incertidumbre.md` 6.1:

- L1.a verbalized confidence: confianza estructurada que reporta el modelo.
- L1.b token-level entropy: entropia media de los logprobs por token cuando
  el proveedor los entrega.
- L1.c hedging features: detector lexico ligero sobre el contenido textual.

Cada subseñal devuelve un valor en `[0, 1]` (mas alto = mas incertidumbre)
o `None` cuando no puede calcularse en este paso (degradacion limpia).
"""

from __future__ import annotations

import math
import re
from typing import Any


class SingleShotSignals:
    """Calcula las tres subseñales single-shot del modelo (L1.a, L1.b, L1.c)."""

    def __init__(
        self,
        hedging_phrases: list[str],
        max_entropy: float = 5.0,
        words_per_hedge: int = 20,
    ):
        """Inicializa parámetros léxicos y de normalización."""
        self._hedging_phrases = [phrase.lower() for phrase in hedging_phrases]
        self._max_entropy = max_entropy
        self._words_per_hedge = max(words_per_hedge, 1)
        self._confidence_pattern = re.compile(
            r"confidence\s*[:=]\s*([0-9]*\.?[0-9]+)",
            re.IGNORECASE,
        )

    def verbalized(self, message: dict[str, Any]) -> float | None:
        """Extrae la confianza verbalizada del modelo en `[0, 1]` invertida a incertidumbre.

        Comprueba tres fuentes en orden de preferencia:
        1. `extra.confidence` — campo estructurado directo (via system_template genérico).
        2. `extra.actions[0].confidence` — campo incluido en el JSON de accion por
           JsonBashModel (el regex sobre content no matchea porque el campo lleva
           comillas JSON antes de los dos puntos: `"confidence": 0.8`).
        3. Regex ligero sobre el contenido textual (fallback de ultimo recurso).

        Devuelve `None` si no es posible extraer un valor valido.
        """
        extra = message.get("extra") or {}

        # 1. Campo estructurado directo.
        raw = extra.get("confidence")
        if isinstance(raw, (int, float)) and 0.0 <= float(raw) <= 1.0:
            return 1.0 - float(raw)

        # 2. Campo en la accion parseada (JsonBashModel guarda confidence en extra.actions).
        actions = extra.get("actions")
        if isinstance(actions, list) and actions and isinstance(actions[0], dict):
            action_conf = actions[0].get("confidence")
            if isinstance(action_conf, (int, float)) and 0.0 <= float(action_conf) <= 1.0:
                return 1.0 - float(action_conf)

        # 3. Fallback: regex sobre el contenido textual del mensaje.
        content = message.get("content") or ""
        match = self._confidence_pattern.search(content)
        if not match:
            return None
        value = float(match.group(1))
        if value > 1.0:
            value = value / 100.0 if value <= 100.0 else 1.0
        if not (0.0 <= value <= 1.0):
            return None
        return 1.0 - value

    def token_entropy(self, message: dict[str, Any]) -> float | None:
        """Calcula la entropia media de los logprobs por token cuando estan disponibles.

        Acepta varias formas habituales en respuestas de OpenAI/LiteLLM:
        - `extra.token_logprobs`: lista de logprobs (escala log natural).
        - `extra.logprobs`: lista de logprobs o estructura `content` con dicts.

        Devuelve `None` si no hay logprobs utilizables.
        """
        logprobs = self._extract_logprobs(message)
        if not logprobs:
            return None
        # Entropia aproximada por token: -E[log p] tomando p = exp(logprob).
        entropies = [-lp for lp in logprobs if isinstance(lp, (int, float)) and not math.isnan(lp)]
        if not entropies:
            return None
        mean_entropy = sum(entropies) / len(entropies)
        return min(mean_entropy / self._max_entropy, 1.0)

    def hedging(self, message: dict[str, Any]) -> float | None:
        """Estima la presencia de hedging lexico en el contenido del mensaje."""
        content = (message.get("content") or "").lower()
        if not content.strip():
            return None
        # Conteo de ocurrencias de cualquier frase de duda registrada.
        hedge_count = sum(content.count(phrase) for phrase in self._hedging_phrases)
        if hedge_count == 0:
            return 0.0
        word_count = max(len(content.split()), 1)
        # Normalizacion: densidad de hedging acotada a [0, 1].
        density = hedge_count / (word_count / self._words_per_hedge)
        return min(density, 1.0)

    def _extract_logprobs(self, message: dict[str, Any]) -> list[float]:
        """Recoge los logprobs por token de varias formas habituales."""
        extra = message.get("extra", {}) or {}
        candidate = extra.get("token_logprobs")
        if isinstance(candidate, list) and candidate:
            return [float(value) for value in candidate if isinstance(value, (int, float))]

        candidate = extra.get("logprobs")
        if isinstance(candidate, list) and candidate:
            return [float(value) for value in candidate if isinstance(value, (int, float))]
        # Estructura tipo OpenAI: {"content": [{"logprob": ...}, ...]}.
        if isinstance(candidate, dict):
            entries = candidate.get("content")
            if isinstance(entries, list):
                return [
                    float(entry["logprob"])
                    for entry in entries
                    if isinstance(entry, dict) and isinstance(entry.get("logprob"), (int, float))
                ]
        return []
