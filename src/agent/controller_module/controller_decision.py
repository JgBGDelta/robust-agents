"""Contrato `ControllerDecision` del modulo controlador."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ActionType = Literal["PROCEED", "INJECT_FEEDBACK", "RUN_VALIDATION", "FINALIZE"]


@dataclass
class ControllerDecision:
    """Decision serializable del controlador para un paso.

    El contrato fija los campos comprometidos por la spec
    `modulo_controlador.md` 8. Los consumidores (`RobustAgent` y el Bloque 2)
    dependen exclusivamente de esta superficie.
    """

    action: ActionType
    subtype: str | None = None
    rationale: str = ""
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Devuelve un diccionario serializable a JSON."""
        return asdict(self)
