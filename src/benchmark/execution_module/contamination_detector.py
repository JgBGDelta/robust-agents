"""`ContaminationDetector` del modulo de ejecucion.

Auditoria post-run (`modulo_ejecucion.md` ME.6.2): busca coincidencias
de `forbidden_test_ids` en la trayectoria del agente y construye los
campos `contamination_detected` y `contamination_evidence` del
`BenchmarkRunRecord`.

Heuristica inicial: **substring matching** sobre el contenido textual
de los mensajes de la traza. Es deliberadamente liberal: un falso
positivo se prefiere a un falso negativo, porque el dato no invalida
el run (el Bloque 3 decide como tratarlo). La heuristica puede
refinarse en futuras versiones (parser de invocaciones a `pytest`,
manejo de identificadores parciales) sin romper el contrato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContaminationMatch:
    """Coincidencia individual encontrada en la traza."""

    test_id: str
    step_index: int
    role: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        """Devuelve la representacion serializable para `contamination_evidence`."""
        return {
            "test_id": self.test_id,
            "step_index": self.step_index,
            "role": self.role,
            "snippet": self.snippet,
        }


@dataclass
class ContaminationResult:
    """Resultado del audit que se vuelca en el `BenchmarkRunRecord`."""

    detected: bool = False
    matches: list[ContaminationMatch] = field(default_factory=list)

    def to_evidence(self) -> dict[str, Any] | None:
        """Devuelve el payload de `contamination_evidence` o `None` si no hay matches."""
        if not self.matches:
            return None
        return {
            "matches": [m.to_dict() for m in self.matches],
            "matched_test_ids": sorted({m.test_id for m in self.matches}),
        }


class ContaminationDetector:
    """Audita una trayectoria contra una lista de `forbidden_test_ids`."""

    SNIPPET_RADIUS = 60
    """Caracteres de contexto a cada lado del match para `evidence`."""

    @staticmethod
    def audit(
        trajectory: dict[str, Any] | None,
        forbidden_test_ids: list[str] | None,
    ) -> ContaminationResult:
        """Devuelve el resultado de la auditoria.

        Si no hay traza o no hay `forbidden_test_ids`, el resultado es
        `detected=False` sin matches: la auditoria no aplica.
        """
        if not trajectory or not forbidden_test_ids:
            return ContaminationResult()

        # Solo se auditan mensajes generados por el agente o el entorno.
        # El system/user prompt podria contener un test id legitimamente
        # (poco probable en SWE-bench) y no debe contar como contaminacion.
        messages = trajectory.get("messages") or []
        auditable_roles = {"assistant", "tool"}

        matches: list[ContaminationMatch] = []
        for idx, message in enumerate(messages):
            role = str(message.get("role", ""))
            if role not in auditable_roles:
                continue
            content = ContaminationDetector._stringify(message)
            if not content:
                continue
            for test_id in forbidden_test_ids:
                if not test_id:
                    continue
                pos = content.find(test_id)
                if pos == -1:
                    continue
                matches.append(
                    ContaminationMatch(
                        test_id=test_id,
                        step_index=idx,
                        role=role,
                        snippet=ContaminationDetector._snippet(content, pos, len(test_id)),
                    )
                )
                # Una marca por (mensaje, test_id): evita inflar evidencia con repeticiones.
        return ContaminationResult(detected=bool(matches), matches=matches)

    # -- Helpers privados ----------------------------------------------------

    @staticmethod
    def _stringify(message: dict[str, Any]) -> str:
        """Concatena los campos textuales relevantes de un mensaje."""
        parts: list[str] = []
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(str(content))
        # En mini-swe-agent, `extra` puede contener `actions` (comandos shell).
        extra = message.get("extra")
        if isinstance(extra, dict):
            actions = extra.get("actions")
            if isinstance(actions, list):
                parts.extend(str(a) for a in actions)
            submission = extra.get("submission")
            if isinstance(submission, str):
                parts.append(submission)
        return "\n".join(parts)

    @staticmethod
    def _snippet(text: str, pos: int, match_len: int) -> str:
        """Auxiliar interno: snippet."""
        start = max(0, pos - ContaminationDetector.SNIPPET_RADIUS)
        end = min(len(text), pos + match_len + ContaminationDetector.SNIPPET_RADIUS)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end]}{suffix}"
