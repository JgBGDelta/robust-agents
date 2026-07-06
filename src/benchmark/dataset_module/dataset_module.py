"""`BenchmarkDatasetModule`: orquestador del modulo de dataset.

Encadena `DatasetLoader` -> `InstanceNormalizer.normalize` ->
`InstanceNormalizer.validate` -> `SliceSelector`. Los filtros por
`instance_ids`/`repos` se aplican en la config del experimento o en el slice.

- `load_instances(dataset_config)` idempotente,
- `get_instance(instance_id)` con lookup directo,
- `selection_summary()` con los metadatos comprometidos para el
  manifiesto del experimento.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from benchmark.config import BenchmarkConfig, DatasetConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.dataset_module.dataset_loader import DatasetLoader, DatasetLoaderProtocol
from benchmark.dataset_module.instance_normalizer import (
    DiscardedInstance,
    InstanceNormalizer,
    ValidationWarning,
)
from benchmark.dataset_module.slice_selector import SliceSelector


class BenchmarkDatasetModule:
    """Carga y normaliza instancias de SWE-bench."""

    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        loader: DatasetLoaderProtocol | None = None,
    ) -> None:
        """Almacena la configuracion y el loader (HF por defecto, inyectable en tests)."""
        self._config = config
        self._loader: DatasetLoaderProtocol = loader or DatasetLoader()
        self._instances: list[BenchmarkInstance] = []
        self._index: dict[str, BenchmarkInstance] = {}
        self._dataset_config: DatasetConfig | None = None
        self._discarded: list[DiscardedInstance] = []
        self._warnings: list[ValidationWarning] = []
        self._num_instances_loaded: int = 0

    # -- API publica ---------------------------------------------------------

    def load_instances(self, dataset_config: DatasetConfig) -> list[BenchmarkInstance]:
        """Carga, normaliza, valida y selecciona el slice del experimento.

        Idempotente dentro del mismo proceso: llamadas sucesivas con el
        mismo `dataset_config` devuelven la misma lista en memoria sin
        consultar a HuggingFace de nuevo.
        """
        if self._dataset_config is not None and self._dataset_config == dataset_config:
            return list(self._instances)

        rows = self._loader.load(
            name=dataset_config.name,
            subset=dataset_config.subset,
            split=dataset_config.split,
        )

        normalized = [
            InstanceNormalizer.normalize(
                row,
                dataset_name=dataset_config.name,
                subset=dataset_config.subset,
                split=dataset_config.split,
            )
            for row in rows
        ]
        self._num_instances_loaded = len(normalized)

        report = InstanceNormalizer.validate(normalized)
        self._discarded = list(report.discarded)
        self._warnings = list(report.warnings)

        candidates = self._apply_instance_id_filter(report.valid, dataset_config)
        sliced = SliceSelector.select(candidates, dataset_config)

        if dataset_config.shuffle and sliced:
            rng = random.Random(dataset_config.slice_seed)
            shuffled = list(sliced)
            rng.shuffle(shuffled)
            sliced = shuffled

        self._abort_if_too_few(sliced, dataset_config)

        self._instances = sliced
        self._index = {inst.instance_id: inst for inst in sliced}
        self._dataset_config = dataset_config
        return list(sliced)

    def get_instance(self, instance_id: str) -> BenchmarkInstance:
        """Devuelve la instancia cargada con `instance_id`. Lanza `KeyError` si no existe."""
        if instance_id not in self._index:
            raise KeyError(f"instancia '{instance_id}' no esta en el slice cargado")
        return self._index[instance_id]

    def selection_summary(self) -> dict[str, Any]:
        """Devuelve los metadatos comprometidos para el `dataset_summary.json`."""
        if self._dataset_config is None:
            return {
                "num_instances_loaded": 0,
                "num_instances_after_filter": 0,
                "instance_ids": [],
                "discarded": [],
            }
        distribution = Counter(inst.repo or "__no_repo__" for inst in self._instances)
        return {
            "dataset_config": self._dataset_config.model_dump(mode="json"),
            "num_instances_loaded": self._num_instances_loaded,
            "num_instances_after_filter": len(self._instances),
            "num_instances_discarded": len(self._discarded),
            "discarded": [d.to_dict() for d in self._discarded],
            "warnings": [w.to_dict() for w in self._warnings],
            "distribution_by_repo": dict(distribution),
            "instance_ids": [inst.instance_id for inst in self._instances],
        }

    # -- Helpers privados ----------------------------------------------------

    @staticmethod
    def _apply_instance_id_filter(
        instances: list[BenchmarkInstance],
        dataset_config: DatasetConfig,
    ) -> list[BenchmarkInstance]:
        """Auxiliar interno: apply instance id filter."""
        if not dataset_config.instance_ids:
            return instances
        allowed = set(dataset_config.instance_ids)
        filtered = [inst for inst in instances if inst.instance_id in allowed]
        missing = allowed - {inst.instance_id for inst in filtered}
        if missing:
            raise RuntimeError(
                "Filtro `instance_ids` abortado: no se encontraron "
                f"{sorted(missing)} en el dataset cargado."
            )
        return filtered

    def _abort_if_too_few(
        self,
        instances: list[BenchmarkInstance],
        dataset_config: DatasetConfig,
    ) -> None:
        """Auxiliar interno: abort if too few."""
        target = dataset_config.slice_size
        if not target or target <= 0:
            return
        if len(instances) < 0.5 * target:
            raise RuntimeError(
                f"Carga del dataset abortada: solo {len(instances)} instancias validas "
                f"frente a una cuota objetivo de {target} (umbral 50%). "
                f"Descartadas: {len(self._discarded)}."
            )
