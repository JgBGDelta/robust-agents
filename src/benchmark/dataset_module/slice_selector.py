"""Selector de slice del dataset.

Implementa las cuatro estrategias de `modulo_dataset.md` MD.5.1:

- `full`: usa todas las instancias normalizadas.
- `sequential`: toma las primeras `slice_size`.
- `random`: muestreo uniforme determinista por semilla.
- `stratified_by_repo`: cuota nominal `ceil(slice_size / num_repos)`
  con redistribucion del excedente entre repos que aun tienen
  instancias por seleccionar (decision D-A.A3 del benchmark).

El selector es **puro**: dada la misma entrada y la misma semilla
devuelve siempre la misma salida.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

from benchmark.config import DatasetConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance


class SliceSelector:
    """Aplica la estrategia de slicing configurada en `DatasetConfig`."""

    @staticmethod
    def select(
        instances: list[BenchmarkInstance],
        dataset_config: DatasetConfig,
    ) -> list[BenchmarkInstance]:
        """Devuelve el subconjunto seleccionado segun `slice_strategy`."""
        # `slice_size=None` o estrategia `full` desactivan el slicing.
        if dataset_config.slice_size is None or dataset_config.slice_strategy == "full":
            return list(instances)

        size = max(0, int(dataset_config.slice_size))
        if size == 0:
            return []

        strategy = dataset_config.slice_strategy
        if strategy == "sequential":
            return list(instances[:size])
        if strategy == "random":
            return SliceSelector._select_random(instances, size, seed=dataset_config.slice_seed)
        if strategy == "stratified_by_repo":
            return SliceSelector._select_stratified_by_repo(
                instances, size, seed=dataset_config.slice_seed
            )
        # Defensivo: el Literal cubre los 4 casos, pero si llega otro valor no rompemos.
        return list(instances[:size])

    # -- Estrategias internas ------------------------------------------------

    @staticmethod
    def _select_random(
        instances: list[BenchmarkInstance],
        size: int,
        *,
        seed: int,
    ) -> list[BenchmarkInstance]:
        """Auxiliar interno: select random."""
        if size >= len(instances):
            return list(instances)
        rng = random.Random(seed)
        return rng.sample(instances, size)

    @staticmethod
    def _select_stratified_by_repo(
        instances: list[BenchmarkInstance],
        size: int,
        *,
        seed: int,
    ) -> list[BenchmarkInstance]:
        """Estratifica por `repo` con cuota nominal y redistribucion."""
        # Inicio: agrupar por repo manteniendo el orden de aparicion.
        groups: dict[str, list[BenchmarkInstance]] = defaultdict(list)
        order: list[str] = []
        for instance in instances:
            key = instance.repo or "__no_repo__"
            if key not in groups:
                order.append(key)
            groups[key].append(instance)

        if not order:
            return []

        rng = random.Random(seed)
        # `target_per_repo` es la cuota nominal (`ceil(size / num_repos)`).
        target_per_repo = max(1, math.ceil(size / len(order)))

        # Primera pasada: muestrear cuota nominal (o todo si el repo es mas pequeno).
        selected: dict[str, list[BenchmarkInstance]] = {}
        leftovers: dict[str, list[BenchmarkInstance]] = {}
        for repo in order:
            available = list(groups[repo])
            rng.shuffle(available)
            take = min(target_per_repo, len(available))
            selected[repo] = available[:take]
            leftovers[repo] = available[take:]

        # Segunda pasada: redistribucion para alcanzar `size` exacto.
        # Si nos pasamos (porque `ceil` excede), recortamos rotativamente; si nos
        # quedamos cortos, repartimos del pool de leftovers en orden ciclico.
        flat = SliceSelector._flatten_in_order(order, selected)
        if len(flat) > size:
            flat = SliceSelector._trim_round_robin(order, selected, target=size)
        elif len(flat) < size:
            flat = SliceSelector._fill_round_robin(order, selected, leftovers, target=size)
        return flat

    # -- Helpers de redistribucion -------------------------------------------

    @staticmethod
    def _flatten_in_order(
        order: list[str],
        selected: dict[str, list[BenchmarkInstance]],
    ) -> list[BenchmarkInstance]:
        """Auxiliar interno: flatten in order."""
        result: list[BenchmarkInstance] = []
        for repo in order:
            result.extend(selected[repo])
        return result

    @staticmethod
    def _trim_round_robin(
        order: list[str],
        selected: dict[str, list[BenchmarkInstance]],
        *,
        target: int,
    ) -> list[BenchmarkInstance]:
        """Recorta de los repos mas grandes hasta llegar a `target`."""
        # Mientras sobre, quitamos un elemento del repo mas largo.
        total = sum(len(items) for items in selected.values())
        while total > target:
            largest = max(order, key=lambda repo: len(selected[repo]))
            if not selected[largest]:
                break
            selected[largest].pop()
            total -= 1
        return SliceSelector._flatten_in_order(order, selected)

    @staticmethod
    def _fill_round_robin(
        order: list[str],
        selected: dict[str, list[BenchmarkInstance]],
        leftovers: dict[str, list[BenchmarkInstance]],
        *,
        target: int,
    ) -> list[BenchmarkInstance]:
        """Rellena ciclicamente con leftovers hasta llegar a `target`."""
        total = sum(len(items) for items in selected.values())
        idx = 0
        while total < target:
            repo = order[idx % len(order)]
            idx += 1
            if leftovers[repo]:
                selected[repo].append(leftovers[repo].pop(0))
                total += 1
                continue
            # Si ningun repo tiene leftovers, no podemos seguir rellenando.
            if not any(leftovers[r] for r in order):
                break
        return SliceSelector._flatten_in_order(order, selected)
