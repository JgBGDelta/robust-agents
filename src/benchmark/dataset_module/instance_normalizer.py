"""Normalizacion y validacion de instancias de SWE-bench.

`InstanceNormalizer` concentra la normalizacion de campos y la validacion
estructural antes del slicing.

Reglas de descarte:
- `instance_id` vacio.
- `problem_statement` vacio o solo espacios.

Advertencias sin descarte:
- `repo` sin `base_commit` o viceversa.
- `gold_patch` que no tiene aspecto de diff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from benchmark.dataset_module.benchmark_instance import BenchmarkInstance


_KNOWN_INSTANCE_FIELDS = frozenset(
    {
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
        "patch",
        "gold_patch",
        "test_patch",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "fail_to_pass",
        "pass_to_pass",
    }
)


@dataclass(frozen=True)
class DiscardedInstance:
    """Instancia descartada con su motivo."""

    instance_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """To dict."""
        return {"instance_id": self.instance_id, "reason": self.reason}


@dataclass(frozen=True)
class ValidationWarning:
    """Advertencia no fatal (no descarta la instancia)."""

    instance_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """To dict."""
        return {"instance_id": self.instance_id, "reason": self.reason}


@dataclass(frozen=True)
class ValidationReport:
    """Resultado de validar un lote de instancias normalizadas."""

    valid: list[BenchmarkInstance]
    discarded: list[DiscardedInstance]
    warnings: list[ValidationWarning]


class InstanceNormalizer:
    """Convierte filas crudas del dataset en `BenchmarkInstance` y las valida."""

    @staticmethod
    def normalize(
        row: dict[str, Any],
        *,
        dataset_name: str,
        subset: str,
        split: str,
    ) -> BenchmarkInstance:
        """Construye un `BenchmarkInstance` a partir de una fila cruda."""
        instance_id = _coerce_str(row.get("instance_id"))
        return BenchmarkInstance(
            instance_id=instance_id,
            dataset_name=dataset_name,
            subset=subset,
            split=split,
            repo=_coerce_optional_str(row.get("repo")),
            base_commit=_coerce_optional_str(row.get("base_commit")),
            problem_statement=_coerce_str(row.get("problem_statement")),
            gold_patch=_coerce_optional_str(row.get("gold_patch") or row.get("patch")),
            test_patch=_coerce_optional_str(row.get("test_patch")),
            fail_to_pass=_coerce_str_list(
                row.get("fail_to_pass") if "fail_to_pass" in row else row.get("FAIL_TO_PASS")
            ),
            pass_to_pass=_coerce_str_list(
                row.get("pass_to_pass") if "pass_to_pass" in row else row.get("PASS_TO_PASS")
            ),
            metadata=_collect_metadata(row),
        )

    @staticmethod
    def validate(instances: list[BenchmarkInstance]) -> ValidationReport:
        """Separa validas de descartadas y emite advertencias por instancia."""
        valid: list[BenchmarkInstance] = []
        discarded: list[DiscardedInstance] = []
        warnings: list[ValidationWarning] = []
        for instance in instances:
            reason = _discard_reason(instance)
            if reason is not None:
                discarded.append(
                    DiscardedInstance(
                        instance_id=instance.instance_id or "<empty>", reason=reason
                    )
                )
                continue
            valid.append(instance)
            warnings.extend(_warnings_for(instance))
        return ValidationReport(valid=valid, discarded=discarded, warnings=warnings)


# ---------------------------------------------------------------------------
# Reglas de validacion (privadas)
# ---------------------------------------------------------------------------


def _discard_reason(instance: BenchmarkInstance) -> str | None:
    """Auxiliar interno: discard reason."""
    if not instance.instance_id:
        return "missing_instance_id"
    if not instance.problem_statement or not instance.problem_statement.strip():
        return "empty_problem_statement"
    return None


def _warnings_for(instance: BenchmarkInstance) -> list[ValidationWarning]:
    """Auxiliar interno: warnings for."""
    out: list[ValidationWarning] = []
    if bool(instance.repo) ^ bool(instance.base_commit):
        out.append(
            ValidationWarning(
                instance_id=instance.instance_id,
                reason="repo_or_base_commit_missing",
            )
        )
    if instance.gold_patch is not None and not _looks_like_diff(instance.gold_patch):
        out.append(
            ValidationWarning(
                instance_id=instance.instance_id,
                reason="gold_patch_not_diff_like",
            )
        )
    return out


def _looks_like_diff(patch: str) -> bool:
    """Auxiliar interno: looks like diff."""
    if not patch.strip():
        return False
    has_header = "diff --git" in patch or ("\n--- " in patch or patch.startswith("--- "))
    has_plus = "\n+++ " in patch or patch.startswith("+++ ")
    return has_header or has_plus


# ---------------------------------------------------------------------------
# Helpers de normalizacion (privados)
# ---------------------------------------------------------------------------


def _coerce_str(value: Any) -> str:
    """Auxiliar interno: coerce str."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _coerce_optional_str(value: Any) -> str | None:
    """Auxiliar interno: coerce optional str."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text or None


def _coerce_str_list(value: Any) -> list[str]:
    """Auxiliar interno: coerce str list."""
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return [stripped]
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
            return [str(parsed)]
        return [stripped]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _collect_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Auxiliar interno: collect metadata."""
    metadata: dict[str, Any] = {}
    for key, value in row.items():
        if key not in _KNOWN_INSTANCE_FIELDS:
            metadata[key] = value
    return metadata
