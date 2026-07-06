import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import litellm
from pydantic import BaseModel

from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.models.utils.actions_toolcall import (
    BASH_TOOL,
    format_toolcall_observation_messages,
    parse_toolcall_actions,
)
from minisweagent.models.utils.anthropic_utils import _reorder_anthropic_thinking_blocks
from minisweagent.models.utils.cache_control import set_cache_control
from minisweagent.models.utils.openai_multimodal import expand_multimodal_content
from minisweagent.models.utils.llm_debug import log_litellm_exchange
from minisweagent.models.utils.retry import retry

logger = logging.getLogger("litellm_model")


# ---------------------------------------------------------------------------
# LiteLLM monkey-patch: fix silent empty choices for Gemini (PRs #24463/#24467)
#
# Bug: LiteLLM ≤1.89.x silently drops Gemini candidates that have no `content`
# field (finishReason=MALFORMED_FUNCTION_CALL from thinking+tool conflict) or
# `content` without `parts` (finishReason=STOP in agentic tasks).  Both cases
# leave model_response.choices=[] with no exception, causing a hard IndexError.
# The upstream fix is not yet on PyPI as of 1.89.1; we backport it here.
# ---------------------------------------------------------------------------
def _patch_litellm_gemini_empty_choices() -> None:
    try:
        from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import (
            VertexGeminiConfig,
        )
        from litellm.types.utils import Choices, Message

        _orig = VertexGeminiConfig._transform_google_generate_content_to_openai_model_response

        def _patched(self, completion_response, model_response, model, logging_obj, raw_response):
            result = _orig(self, completion_response, model_response, model, logging_obj, raw_response)
            if not result.choices:
                _cands = completion_response.get("candidates") if hasattr(completion_response, "get") else None
                if _cands:
                    _fr = _cands[0].get("finishReason", "stop")
                    result.choices = [
                        Choices(
                            finish_reason=VertexGeminiConfig._check_finish_reason(None, _fr),
                            index=0,
                            message=Message(role="assistant", content=None),
                        )
                    ]
                    logger.debug(
                        "litellm_patch: synthesised choice finish_reason=%s (raw finishReason=%s)",
                        result.choices[0].finish_reason, _fr,
                    )
            return result

        VertexGeminiConfig._transform_google_generate_content_to_openai_model_response = _patched
    except Exception as exc:  # noqa: BLE001
        logger.warning("litellm_patch: could not apply Gemini empty-choices fix: %s", exc)


_patch_litellm_gemini_empty_choices()


class EmptyModelResponseError(RuntimeError):
    """Raised when the API returns a response without any choices (retryable)."""


class LitellmModelConfig(BaseModel):
    model_name: str
    """Model name. Highly recommended to include the provider in the model name, e.g., `anthropic/claude-sonnet-4-5-20250929`."""
    model_kwargs: dict[str, Any] = {}
    """Additional arguments passed to the API."""
    litellm_model_registry: Path | str | None = os.getenv("LITELLM_MODEL_REGISTRY_PATH")
    """Model registry for cost tracking and model metadata. See the local model guide (https://mini-swe-agent.com/latest/models/local_models/) for more details."""
    set_cache_control: Literal["default_end"] | None = None
    """Set explicit cache control markers, for example for Anthropic models"""
    cost_tracking: Literal["default", "ignore_errors"] = os.getenv("MSWEA_COST_TRACKING", "default")
    """Cost tracking mode for this model. Can be "default" or "ignore_errors" (ignore errors/missing cost info)"""
    format_error_template: str = "{{ error }}"
    """Template used when the LM's output is not in the expected format."""
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """Template used to render the observation after executing an action."""
    multimodal_regex: str = ""
    """Regex to extract multimodal content. Empty string disables multimodal processing."""


class LitellmModel:
    abort_exceptions: list[type[Exception]] = [
        litellm.exceptions.UnsupportedParamsError,
        litellm.exceptions.NotFoundError,
        litellm.exceptions.PermissionDeniedError,
        litellm.exceptions.ContextWindowExceededError,
        litellm.exceptions.AuthenticationError,
        KeyboardInterrupt,
    ]

    def __init__(self, *, config_class: Callable = LitellmModelConfig, **kwargs):
        self.config = config_class(**kwargs)
        self._call_index = 0
        if self.config.litellm_model_registry and Path(self.config.litellm_model_registry).is_file():
            litellm.utils.register_model(json.loads(Path(self.config.litellm_model_registry).read_text()))

    def _query(self, messages: list[dict[str, str]], **kwargs):
        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=[BASH_TOOL],
                **(self.config.model_kwargs | kwargs),
            )
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise e

    def _prepare_messages_for_api(self, messages: list[dict]) -> list[dict]:
        prepared = [{k: v for k, v in msg.items() if k != "extra"} for msg in messages]
        prepared = _reorder_anthropic_thinking_blocks(prepared)
        return set_cache_control(prepared, mode=self.config.set_cache_control)

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self._call_index += 1
        call_index = self._call_index
        prepared = self._prepare_messages_for_api(messages)
        model_kwargs = dict(self.config.model_kwargs | kwargs)
        response = None
        empty_retries = 0
        for attempt in retry(logger=logger, abort_exceptions=self.abort_exceptions):
            with attempt:
                retry_attempt = attempt.retry_state.attempt_number
                response = self._query(prepared, **kwargs)
                # Detect empty or degenerate responses (retryable).
                # "Degenerate" = synthesised fallback choice from our LiteLLM patch:
                # finish_reason is not tool_calls AND content is None/empty AND no tool_calls.
                _choices = getattr(response, "choices", None) or []
                _empty = not _choices
                if not _empty and _choices:
                    _msg = _choices[0].message
                    _degenerate = (
                        not getattr(_msg, "tool_calls", None)
                        and not (getattr(_msg, "content", None) or "").strip()
                    )
                    _empty = _degenerate
                if _empty:
                    empty_retries += 1
                    _fr = _choices[0].finish_reason if _choices else "N/A"
                    log_litellm_exchange(
                        event="empty_choices",
                        model_name=self.config.model_name,
                        call_index=call_index,
                        retry_attempt=retry_attempt,
                        messages=prepared,
                        response=response,
                        model_kwargs=model_kwargs,
                        error=f"choices list is empty or degenerate (finish_reason={_fr})",
                    )
                    raise EmptyModelResponseError(
                        f"Model {self.config.model_name} returned empty/degenerate response "
                        f"(finish_reason={_fr}, "
                        f"prompt_tokens={getattr(getattr(response, 'usage', None), 'prompt_tokens', '?')})"
                    )
        assert response is not None
        log_litellm_exchange(
            event="completion",
            model_name=self.config.model_name,
            call_index=call_index,
            retry_attempt=empty_retries,
            messages=prepared,
            response=response,
            model_kwargs=model_kwargs,
        )
        cost_output = self._calculate_cost(response)
        GLOBAL_MODEL_STATS.add(cost_output["cost"])
        message = response.choices[0].message.model_dump()
        message["extra"] = {
            "actions": self._parse_actions(response),
            "response": response.model_dump(),
            **cost_output,
            "timestamp": time.time(),
        }
        if empty_retries:
            message["extra"]["empty_choice_retries"] = empty_retries
        return message

    def _calculate_cost(self, response) -> dict[str, float]:
        try:
            cost = litellm.cost_calculator.completion_cost(response, model=self.config.model_name)
            if cost <= 0.0:
                raise ValueError(f"Cost must be > 0.0, got {cost}")
        except Exception as e:
            cost = 0.0
            if self.config.cost_tracking != "ignore_errors":
                msg = (
                    f"Error calculating cost for model {self.config.model_name}: {e}, perhaps it's not registered? "
                    "You can ignore this issue from your config file with cost_tracking: 'ignore_errors' or "
                    "globally with export MSWEA_COST_TRACKING='ignore_errors'. "
                    "Alternatively check the 'Cost tracking' section in the documentation at "
                    "https://klieret.short.gy/mini-local-models. "
                    " Still stuck? Please open a github issue at https://github.com/SWE-agent/mini-swe-agent/issues/new/choose!"
                )
                logger.critical(msg)
                raise RuntimeError(msg) from e
        return {"cost": cost}

    def _parse_actions(self, response) -> list[dict]:
        """Parse tool calls from the response. Raises FormatError if unknown tool."""
        tool_calls = response.choices[0].message.tool_calls or []
        return parse_toolcall_actions(tool_calls, format_error_template=self.config.format_error_template)

    def format_message(self, **kwargs) -> dict:
        return expand_multimodal_content(kwargs, pattern=self.config.multimodal_regex)

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """Format execution outputs into tool result messages."""
        actions = message.get("extra", {}).get("actions", [])
        return format_toolcall_observation_messages(
            actions=actions,
            outputs=outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
            multimodal_regex=self.config.multimodal_regex,
        )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump()

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }
