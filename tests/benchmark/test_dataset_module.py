"""Tests unitarios del `BenchmarkDatasetModule`.

Sin red: el cargador de HuggingFace se sustituye por un fake. Cubren
normalizacion del formato SWE-bench oficial (campos en mayusculas y
JSON-string), las cuatro estrategias de slicing, validacion (descartes
y advertencias) en `InstanceNormalizer`, y la API publica del modulo
(`load_instances`, `get_instance`, `selection_summary`, idempotencia,
aborto al >50% descartado).
"""

from __future__ import annotations

import json

import pytest

from benchmark.config import BenchmarkConfig, DatasetConfig
from benchmark.dataset_module import BenchmarkDatasetModule, BenchmarkInstance
from benchmark.dataset_module.dataset_loader import DatasetLoaderProtocol
from benchmark.dataset_module.instance_normalizer import (
    DiscardedInstance,
    InstanceNormalizer,
    ValidationReport,
    ValidationWarning,
)
from benchmark.dataset_module.slice_selector import SliceSelector


# --- Fakes y fabricas auxiliares ------------------------------------------------


class FakeLoader:
    """`DatasetLoaderProtocol` en memoria para tests sin red."""

    def __init__(self, rows: list[dict]) -> None:
        """Inicializa `FakeLoader`."""
        self.rows = rows
        self.calls: list[tuple[str, str, str]] = []

    def load(self, *, name: str, subset: str, split: str) -> list[dict]:
        """Load."""
        self.calls.append((name, subset, split))
        return [dict(row) for row in self.rows]


def _swebench_row(
    *,
    instance_id: str,
    repo: str = "django/django",
    problem: str = "fix something important",
    fail_to_pass_json: bool = True,
    pass_to_pass: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Auxiliar interno: swebench row."""
    fail_value = json.dumps(["test_a", "test_b"]) if fail_to_pass_json else ["test_a", "test_b"]
    pass_value = pass_to_pass if pass_to_pass is not None else ["test_c"]
    pass_value_serialized = json.dumps(pass_value) if fail_to_pass_json else pass_value
    row = {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": "0123abcd",
        "problem_statement": problem,
        "patch": "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@\n-old\n+new\n",
        "test_patch": "diff --git a/test b/test\n",
        "FAIL_TO_PASS": fail_value,
        "PASS_TO_PASS": pass_value_serialized,
        "version": "1.2",
        "hints_text": "hint",
        "environment_setup_commit": "deadbeef",
    }
    if extra:
        row.update(extra)
    return row


def _instance(
    *,
    instance_id: str,
    repo: str | None = "django/django",
    problem: str = "fix something",
) -> BenchmarkInstance:
    """Auxiliar interno: instance."""
    return BenchmarkInstance(
        instance_id=instance_id,
        dataset_name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        repo=repo,
        base_commit="abc",
        problem_statement=problem,
        gold_patch="diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
    )


def _config(**kwargs: object) -> DatasetConfig:
    """Auxiliar interno: config."""
    return DatasetConfig(**kwargs)  # type: ignore[arg-type]


def _module(loader: FakeLoader, config: BenchmarkConfig | None = None) -> BenchmarkDatasetModule:
    """Auxiliar interno: module."""
    return BenchmarkDatasetModule(config or BenchmarkConfig(), loader=loader)


# --- Normalizacion --------------------------------------------------------------


def test_normalizer_extracts_gold_patch_from_patch_alias():
    """Comprueba que normalizer extracts gold patch from patch alias."""
    row = _swebench_row(instance_id="inst-a")
    inst = InstanceNormalizer.normalize(
        row, dataset_name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"
    )
    assert inst.gold_patch is not None
    assert "diff --git" in inst.gold_patch


def test_normalizer_parses_fail_to_pass_json_string():
    """Comprueba que normalizer parses fail to pass json string."""
    row = _swebench_row(instance_id="inst-a", fail_to_pass_json=True)
    inst = InstanceNormalizer.normalize(
        row, dataset_name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"
    )
    assert inst.fail_to_pass == ["test_a", "test_b"]


def test_normalizer_accepts_native_list_fail_to_pass():
    """Comprueba que normalizer accepts native list fail to pass."""
    row = _swebench_row(instance_id="inst-a", fail_to_pass_json=False)
    inst = InstanceNormalizer.normalize(
        row, dataset_name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"
    )
    assert inst.fail_to_pass == ["test_a", "test_b"]


def test_normalizer_collects_extra_fields_in_metadata():
    """Comprueba que normalizer collects extra fields in metadata."""
    row = _swebench_row(instance_id="inst-a", extra={"custom_field": "custom_value"})
    inst = InstanceNormalizer.normalize(
        row, dataset_name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"
    )
    assert inst.metadata.get("custom_field") == "custom_value"


# --- Validacion en InstanceNormalizer -------------------------------------------


def test_validator_discards_empty_instance_id():
    """Comprueba que validator discards empty instance id."""
    instances = [
        _instance(instance_id=""),
        _instance(instance_id="valid-id"),
    ]
    report = InstanceNormalizer.validate(instances)
    assert len(report.valid) == 1
    assert report.valid[0].instance_id == "valid-id"
    assert any(d.reason == "missing_instance_id" for d in report.discarded)


def test_validator_discards_empty_problem_statement():
    """Comprueba que validator discards empty problem statement."""
    instances = [_instance(instance_id="inst-a", problem="   ")]
    report = InstanceNormalizer.validate(instances)
    assert len(report.valid) == 0
    assert report.discarded[0].reason == "empty_problem_statement"


def test_validator_returns_warning_for_missing_base_commit():
    """Comprueba que validator returns warning for missing base commit."""
    instances = [
        BenchmarkInstance(
            instance_id="inst-a",
            dataset_name="ds",
            subset="lite",
            split="test",
            repo="org/repo",
            base_commit=None,
            problem_statement="Fix bug",
            gold_patch="diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        )
    ]
    report = InstanceNormalizer.validate(instances)
    assert len(report.valid) == 1
    assert any(w.reason == "repo_or_base_commit_missing" for w in report.warnings)


def test_validator_no_discards_on_valid_instances():
    """Comprueba que validator no discards on valid instances."""
    instances = [_instance(instance_id=f"inst-{i}") for i in range(5)]
    report = InstanceNormalizer.validate(instances)
    assert len(report.valid) == 5
    assert len(report.discarded) == 0


# --- Dataset module: carga idempotente ------------------------------------------


def test_load_instances_calls_hf_loader():
    """Comprueba que load instances calls hf loader."""
    rows = [_swebench_row(instance_id=f"inst-{i}", repo="django/django") for i in range(5)]
    loader = FakeLoader(rows)
    module = _module(loader)
    module.load_instances(_config(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"))
    assert len(loader.calls) == 1
    assert loader.calls[0] == ("SWE-bench/SWE-bench_Lite", "lite", "test")


def test_load_instances_idempotent():
    """Comprueba que load instances idempotent."""
    rows = [_swebench_row(instance_id=f"inst-{i}") for i in range(3)]
    loader = FakeLoader(rows)
    module = _module(loader)
    cfg = _config(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test")
    a = module.load_instances(cfg)
    b = module.load_instances(cfg)
    assert len(loader.calls) == 1
    assert [inst.instance_id for inst in a] == [inst.instance_id for inst in b]


def test_get_instance_returns_correct_instance():
    """Comprueba que get instance returns correct instance."""
    rows = [_swebench_row(instance_id="inst-a"), _swebench_row(instance_id="inst-b")]
    loader = FakeLoader(rows)
    module = _module(loader)
    module.load_instances(_config(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"))
    inst = module.get_instance("inst-a")
    assert inst.instance_id == "inst-a"


def test_get_instance_raises_on_unknown():
    """Comprueba que get instance raises on unknown."""
    rows = [_swebench_row(instance_id="inst-a")]
    loader = FakeLoader(rows)
    module = _module(loader)
    module.load_instances(_config(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"))
    with pytest.raises(KeyError, match="unknown-id"):
        module.get_instance("unknown-id")


# --- Slicing y seleccion --------------------------------------------------------


def test_slice_selector_stratified_balances_repos():
    """Comprueba que slice selector stratified balances repos."""
    instances = (
        [_instance(instance_id=f"dj-{i}", repo="django/django") for i in range(10)]
        + [_instance(instance_id=f"fl-{i}", repo="flask/flask") for i in range(10)]
    )
    cfg = _config(slice_strategy="stratified_by_repo", slice_size=4, slice_seed=42)
    selected = SliceSelector.select(instances, cfg)
    repos = {inst.repo for inst in selected}
    assert len(selected) == 4
    assert len(repos) == 2


def test_slice_selector_sequential():
    """Comprueba que slice selector sequential."""
    instances = [_instance(instance_id=f"inst-{i}") for i in range(10)]
    cfg = _config(slice_strategy="sequential", slice_size=3)
    selected = SliceSelector.select(instances, cfg)
    assert [inst.instance_id for inst in selected] == ["inst-0", "inst-1", "inst-2"]


def test_slice_selector_full_returns_all():
    """Comprueba que slice selector full returns all."""
    instances = [_instance(instance_id=f"inst-{i}") for i in range(5)]
    cfg = _config(slice_strategy="full")
    selected = SliceSelector.select(instances, cfg)
    assert len(selected) == 5


# --- selection_summary ----------------------------------------------------------


def test_selection_summary_has_required_keys():
    """Comprueba que selection summary has required keys."""
    rows = [_swebench_row(instance_id=f"inst-{i}") for i in range(3)]
    loader = FakeLoader(rows)
    module = _module(loader)
    module.load_instances(_config(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"))
    summary = module.selection_summary()
    assert "num_instances_loaded" in summary
    assert "num_instances_after_filter" in summary
    assert "instance_ids" in summary
    assert "discarded" in summary


def test_selection_summary_no_filter_key():
    """DatasetConfig sin campo `filter`; `selection_summary` tampoco lo expone."""
    rows = [_swebench_row(instance_id="inst-a")]
    loader = FakeLoader(rows)
    module = _module(loader)
    module.load_instances(_config(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"))
    summary = module.selection_summary()
    assert "filter" not in summary


# --- Aborto por >50% descartado -------------------------------------------------


def test_module_aborts_when_more_than_50_percent_discarded():
    """Comprueba que module aborts when more than 50 percent discarded."""
    valid_rows = [_swebench_row(instance_id=f"inst-{i}") for i in range(3)]
    invalid_rows = [
        {"instance_id": "", "problem_statement": "broken", "repo": "r", "base_commit": "x",
         "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]"}
        for _ in range(10)
    ]
    loader = FakeLoader(valid_rows + invalid_rows)
    module = _module(loader)
    cfg = _config(
        name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        slice_strategy="full",
        slice_size=10,
    )
    with pytest.raises(RuntimeError, match="50%"):
        module.load_instances(cfg)


# --- Filtro por instance_ids ----------------------------------------------------


def test_instance_ids_filter_keeps_only_requested():
    """Comprueba que instance ids filter keeps only requested."""
    rows = [
        _swebench_row(instance_id="astropy__astropy-12907", repo="astropy/astropy"),
        _swebench_row(instance_id="django__django-11001", repo="django/django"),
    ]
    module = _module(FakeLoader(rows))
    cfg = _config(
        name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        slice_strategy="full",
        instance_ids=["astropy__astropy-12907"],
    )
    instances = module.load_instances(cfg)
    assert [i.instance_id for i in instances] == ["astropy__astropy-12907"]


def test_instance_ids_filter_raises_when_id_missing():
    """Comprueba que instance ids filter raises when id missing."""
    rows = [_swebench_row(instance_id="django__django-11001")]
    module = _module(FakeLoader(rows))
    cfg = _config(
        slice_strategy="full",
        instance_ids=["astropy__astropy-12907"],
    )
    with pytest.raises(RuntimeError, match="astropy__astropy-12907"):
        module.load_instances(cfg)


# --- DatasetConfig sin filtro post-slice ----------------------------------------


def test_dataset_config_has_no_filter_field():
    """Comprueba que dataset config has no filter field."""
    cfg = DatasetConfig()
    assert not hasattr(cfg, "filter")


def test_no_instance_filter_module():
    """Comprueba que no instance filter module."""
    with pytest.raises(ImportError):
        import benchmark.dataset_module.instance_filter  # noqa: F401


def test_no_instance_validator_module():
    """Comprueba que no instance validator module."""
    with pytest.raises(ImportError):
        import benchmark.dataset_module.instance_validator  # noqa: F401
