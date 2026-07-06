"""Parseo del JSONL de predicciones en formato estandar SWE-bench.

Valida que cada linea tenga los campos obligatorios (``instance_id`` y
``model_patch``); las que no los tienen se descartan con un aviso en log,
sin abortar la carga completa.

Acoplamientos:
- Usado por `ExternalMetricsModule.load_patches()`.
- Produce `ExternalPatchRecord`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from analysis.external_metrics_module.external_patch_record import ExternalPatchRecord

logger = logging.getLogger(__name__)


class JsonlLoader:
    """Parsea linea a linea el JSONL de predicciones de un agente externo."""

    def load(self, path: Path) -> list[ExternalPatchRecord]:
        """Lee ``path`` y devuelve la lista de registros validos.

        Las lineas que no sean JSON valido o que carezcan de ``instance_id``
        o ``model_patch`` se descartan silenciosamente (se emite un warning
        con conteo al final).

        Parametros
        ----------
        path:
            Ruta al JSONL de predicciones.

        Retorna
        -------
        Lista de ``ExternalPatchRecord`` uno por linea valida.
        """
        records: list[ExternalPatchRecord] = []
        discarded = 0
        total = 0

        for lineno, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line:
                continue
            total += 1
            try:
                data = json.loads(line)
                records.append(ExternalPatchRecord.from_dict(data))
            except (json.JSONDecodeError, KeyError) as exc:
                discarded += 1
                logger.debug(
                    "Linea %d descartada (%s): %.120s", lineno, type(exc).__name__, line
                )

        if discarded:
            logger.warning(
                "%s: %d/%d lineas descartadas por formato invalido "
                "(faltan 'instance_id' o 'model_patch', o JSON malformado).",
                path,
                discarded,
                total,
            )
        else:
            logger.info("%s: %d registros cargados.", path, len(records))

        return records
