"""Emparejamiento de parches externos con las instancias del experimento.

Indexa los ``ExternalPatchRecord`` por ``instance_id`` y los cruza contra
la lista de instancias del experimento. El caso esperado es 100% de match
(las instancias del experimento son subconjunto de las 300 del Lite completo),
pero el modulo maneja explicitamente el caso contrario para no asumir
invariantes que podrian romperse si la fuente cambia su release.

Acoplamientos:
- Usado por `ExternalMetricsModule.extract_metrics()`.
- Consume `ExternalPatchRecord`, no interactua con disco.
"""

from __future__ import annotations

import logging

from analysis.external_metrics_module.external_patch_record import ExternalPatchRecord

logger = logging.getLogger(__name__)


class Matcher:
    """Empareja predicciones externas contra instancias del experimento."""

    def match(
        self,
        patches: list[ExternalPatchRecord],
        instance_ids: list[str],
    ) -> dict[str, ExternalPatchRecord | None]:
        """Devuelve ``{instance_id: record_or_None}`` para cada id del experimento.

        ``None`` en el valor indica que la fuente externa no publico parche
        para esa instancia (``matched=False`` en el ``ExternalMetricsRecord``
        resultante).

        Parametros
        ----------
        patches:
            Predicciones cargadas desde el JSONL de la fuente externa.
        instance_ids:
            Conjunto de instancias del experimento a emparejar.

        Retorna
        -------
        Diccionario con una entrada por cada ``instance_id`` de entrada.
        """
        # Indice de parches por instance_id para busqueda O(1).
        index: dict[str, ExternalPatchRecord] = {p.instance_id: p for p in patches}

        if len(index) < len(patches):
            logger.warning(
                "El JSONL contiene instance_ids duplicados; se conserva el ultimo."
            )

        result: dict[str, ExternalPatchRecord | None] = {}
        unmatched: list[str] = []

        for iid in instance_ids:
            record = index.get(iid)
            result[iid] = record
            if record is None:
                unmatched.append(iid)

        if unmatched:
            logger.warning(
                "%d/%d instancias sin contraparte en la fuente externa: %s",
                len(unmatched),
                len(instance_ids),
                unmatched[:10],  # Muestra solo los primeros 10 para no saturar el log.
            )
        else:
            logger.info(
                "Match completo: %d/%d instancias emparejadas.",
                len(instance_ids),
                len(instance_ids),
            )

        return result
