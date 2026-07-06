"""JSON-mode bash model: herramienta de bash via texto JSON en lugar de tool_calls nativos.

Soluciona el bug de Gemini que devuelve respuestas vacias (finish_reason=stop,
content=None) en conversaciones largas con tool-calling activado. Al eliminar
el parametro `tools` de la llamada a la API, Gemini solo genera texto ordinario,
que es mucho mas fiable a cualquier longitud de contexto.

Protocolo de comunicacion:
  - El modelo responde siempre con un JSON de una sola linea:
      {"command": "<comando bash aqui>"}
  - Las observaciones (outputs de comandos) se inyectan como mensajes `user`
    en lugar de mensajes `tool`, eliminando el protocolo tool_call_id.

Uso: configurar `model_class` en el YAML de mini-swe-agent:
    model:
      model_class: agent.models.json_bash_model.JsonBashModel
"""

from __future__ import annotations

import json
import re
from typing import Any

import litellm

from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel

_JSON_TOOL_CALL_ID = "call_json_bash"

_JSON_FORMAT_REMINDER = (
    'Respond with exactly one JSON object: {"command": "<bash command>"}\n'
    "No text before or after. Use && to chain commands if needed."
)


def _extract_command(content: str) -> str | None:
    """Extrae el comando bash de la respuesta JSON del modelo.

    Intenta parsear JSON directo; si falla, busca un objeto {"command": ...}
    en el texto (tolerante a markdown code fences y texto extra).
    """
    if not content:
        return None
    text = content.strip()

    # Eliminar code fences de markdown si el modelo los incluyó
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Intento 1: parseo JSON directo del texto completo
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "command" in data:
            return str(data["command"])
    except json.JSONDecodeError:
        pass

    # Intento 2: extraer el primer objeto {"command": "..."} del texto
    # (útil si el modelo añade texto antes/después del JSON)
    match = re.search(
        r'\{\s*"command"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        text,
        re.DOTALL,
    )
    if match:
        raw = match.group(1)
        # Desescapar secuencias JSON básicas
        return raw.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")

    return None


def _extract_confidence(content: str) -> float | None:
    """Extrae el valor de confianza del campo opcional del JSON de respuesta.

    Devuelve un float en [0, 1] o None si el campo no está presente o es inválido.
    """
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            value = data.get("confidence")
            if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
                return float(value)
    except json.JSONDecodeError:
        pass
    return None


class JsonBashModel(LitellmModel):
    """Variante de LitellmModel que usa respuestas JSON en texto plano.

    - No envía `tools` a la API → elimina el conflicto tool_calling/Gemini.
    - Inyecta un recordatorio de formato JSON al final del mensaje system.
    - Parsea el comando desde el contenido de texto en lugar de `tool_calls`.
    - Convierte los mensajes `tool` (outputs de comandos) a mensajes `user`.
    """

    # ------------------------------------------------------------------
    # Override: llamada a la API sin tools

    def _query(self, messages: list[dict], **kwargs) -> Any:
        """Llama a litellm sin el parametro tools; convierte mensajes tool→user."""
        converted = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                # Añadir recordatorio de formato JSON al system prompt
                content = (msg.get("content") or "") + "\n\n" + _JSON_FORMAT_REMINDER
                converted.append({**msg, "content": content})
            elif role == "tool":
                # Los resultados de herramienta se convierten en mensajes user
                converted.append({"role": "user", "content": msg.get("content", "")})
            else:
                # Eliminar tool_calls de mensajes assistant (si los hubiera)
                converted.append({k: v for k, v in msg.items() if k != "tool_calls"})

        # Eliminar parámetros de tool-calling del model_kwargs y kwargs
        _excluded = {"tools", "tool_choice"}
        clean_model_kwargs = {k: v for k, v in self.config.model_kwargs.items() if k not in _excluded}
        clean_kwargs = {k: v for k, v in kwargs.items() if k not in _excluded}

        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=converted,
                **(clean_model_kwargs | clean_kwargs),
            )
        except litellm.exceptions.AuthenticationError as exc:
            exc.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise exc

    # ------------------------------------------------------------------
    # Override: parsear comando desde JSON en el contenido de texto

    def _parse_actions(self, response) -> list[dict]:
        """Extrae el comando bash del JSON de texto en lugar de tool_calls."""
        content = (response.choices[0].message.content or "").strip()
        command = _extract_command(content)
        if command is None:
            error_msg = f"No valid JSON command found in response. Got: {content[:300]!r}"
            template = self.config.format_error_template
            if template:
                error_content = template.format(error=error_msg, actions=[])
            else:
                # Fallback sin template: no usar .format() sobre _JSON_FORMAT_REMINDER
                # porque contiene { } que serían interpretados como placeholders.
                error_content = f"{_JSON_FORMAT_REMINDER}\n\nError: {error_msg}"
            raise FormatError(
                {
                    "role": "user",
                    "content": error_content,
                    "extra": {"interrupt_type": "FormatError"},
                }
            )
        # Formato compatible con parse_toolcall_actions y DockerEnvironment.execute():
        # la clave "command" debe estar en el nivel raíz del dict de acción.
        # "confidence" es opcional: None si el modelo no lo incluyó.
        return [{"command": command, "confidence": _extract_confidence(content), "tool_call_id": _JSON_TOOL_CALL_ID}]

    # ------------------------------------------------------------------
    # Override: observaciones como mensajes user en lugar de tool

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """Formatea los outputs de comandos como mensajes user (no tool)."""
        tool_messages = super().format_observation_messages(message, outputs, template_vars)
        result = []
        for msg in tool_messages:
            if msg.get("role") == "tool":
                result.append({"role": "user", "content": msg.get("content", "")})
            else:
                result.append(msg)
        return result
