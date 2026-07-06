"""Contrato de salida: una fila de `datos_consolidados.csv` por run logico.

Esquema completo cerrado en `especificacion_analisis.md`
S6.2. Un `source_type == "own"` viene de `runs/<experiment_id>/instances/**`; un
`source_type == "external"` viene de un `ExternalMetricsRecord`. Ambos comparten
exactamente las mismas columnas; los campos no disponibles para una fuente quedan
a `None` (degradacion limpia, nunca se omite la fila ni la columna).

Acoplamientos:
- Producido por `RowBuilder`.
- Consumido por `ConsolidationModule.write_csv()` y, via CSV, por `DiagramsModule`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class ConsolidatedRunRow:
    """Fila de `datos_consolidados.csv` (una por run logico, propio o externo)."""

    # -- Identificacion (S6.2 "Identificacion") --------------------------------
    run_id: str
    instance_id: str
    repository: str
    agent_id: str
    configuration_id: str
    source_type: str
    model_id: str | None
    sample_group: str

    # -- Estado y resultado (S6.2 "Estado y resultado") --------------------------
    run_status: str | None
    exit_status: str | None
    termination: str | None
    failure_category: str | None
    patch_available: bool
    submitted: bool
    empty_submission: bool | None
    evaluable: bool | None
    resolved: bool | None

    # -- Coste y esfuerzo (S6.2 "Coste y esfuerzo", solo `own`) -------------------
    steps_used: int | None
    cost_usd: float | None
    duration_seconds: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    run_sequence: int | None
    retry_count: int | None
    started_at: float | None
    completed_at: float | None

    # -- Incertidumbre (S6.2, solo `own` + traza `robust_agent`) ------------------
    uncertainty_mean: float | None
    uncertainty_max: float | None
    uncertainty_final: float | None
    uncertainty_std: float | None
    uncertainty_p95: float | None
    uncertainty_high_ratio: float | None
    cycle_mean: float | None
    failure_rate_mean: float | None
    hedging_mean: float | None
    volatility_mean: float | None
    uncertainty_missing_components: int | None

    # -- Riesgo estructural (S6.2, solo `own` + traza `robust_agent`) -------------
    structural_risk_mean: float | None
    structural_risk_max: float | None
    structural_risk_final: float | None
    structural_risk_p95: float | None
    structural_risk_high_ratio: float | None
    files_changed_ratio: float | None
    hunks_per_file: float | None
    net_lines: int | None
    cumulative_lines_changed: int | None
    surface_final_size: int | None

    # -- Controlador (S6.2, solo `own` + traza `robust_agent`) --------------------
    controller_intervention_count: int | None
    proceed_count: int | None
    feedback_count: int | None
    validation_count: int | None
    abort_count: int | None
    first_intervention_step: int | None
    controller_intervention_ratio: float | None
    high_high_encounter_count: int | None
    final_controller_action: str | None
    validation_success_count: int | None
    validation_failure_count: int | None

    # -- Parche (S6.2, `own` + `external`, via PatchMetricsExtractor extendido) --
    files_modified_count: int | None
    lines_added: int | None
    lines_deleted: int | None
    churn_total: int | None
    hunks_count: int | None
    dispersion_score: float | None
    test_touch_ratio: float | None

    # -- Comparacion con golden patch (S6.2) --------------------------------------
    matching_files_count: int | None
    unexpected_files_count: int | None
    missing_files_count: int | None
    jaccard_files: float | None
    jaccard_lines: float | None

    # -- Procedencia (S6.2) -------------------------------------------------------
    patch_path: str | None
    trajectory_path: str | None
    evaluation_path: str | None
    external_release: str | None
    external_source: str | None
    external_model_id: str | None

    def to_dict(self) -> dict[str, Any]:
        """Diccionario serializable a CSV (orden estable de columnas)."""
        return asdict(self)

    @classmethod
    def fieldnames(cls) -> list[str]:
        """Nombres de columna en el orden declarado (para `csv.DictWriter`)."""
        return [f.name for f in fields(cls)]
