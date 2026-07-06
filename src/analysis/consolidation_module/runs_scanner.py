"""`RunsScanner`: escaneo del arbol `runs/<experiment_id>/instances/**` y
seleccion del run canonico ante cadenas de reintentos.

Implementa el algoritmo de `especificacion_analisis.md` S6.3.1:

1. Enumerar carpetas de run que contengan `run_record.json` (las que no lo
   tienen se ignoran por completo, S6 "Politica ante fallos").
2. Agrupar por `(instance_id, agent_id)` + raiz de hash `r-<hash>` (el sufijo
   `-rN` no cambia de grupo).
3. Elegir como canonico el de mayor `N` dentro de cada grupo.
4. `retry_count` = numero de directorios del grupo - 1.
5. `run_sequence` = `N` del run canonico (0 si no tiene sufijo).

Nota de diseno (dato real observado sobre `runs/full-lite-120inst-6agents/`):
el fallback automatico Vertex AI <-> AI Studio puede hacer que un mismo
`(instance_id, agent_id)` tenga dos carpetas de run con **raices de hash
distintas** (el `model_id` efectivo cambia el hash determinista del `run_id`).
Ese caso no comparte raiz de hash, así que el algoritmo anterior los trata
como dos runs logicos independientes (cada uno con `retry_count=0`), no como
una cadena de reintentos. Es el comportamiento documentado en
`especificacion_analisis.md` S6.1: la fusion de ambos bajo el mismo agente se
resuelve a nivel de `configuration_id` (via `model_id` normalizado), no a
nivel de fila.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_RUN_DIR_RE = re.compile(r"^r-(?P<hash>[0-9a-f]{8})(?:-r(?P<seq>\d+))?$")


@dataclass
class ScannedRun:
    """Run logico canonico listo para que `RowBuilder` construya su fila."""

    instance_id: str
    agent_id: str
    run_dir: Path
    run_sequence: int
    retry_count: int


class RunsScanner:
    """Recorre `runs_root/instances/*/*/*/` y devuelve runs logicos canonicos."""

    def scan(self, runs_root: Path) -> list[ScannedRun]:
        """Escanea `runs_root/instances/**` y aplica la seleccion canonica.

        Parametros
        ----------
        runs_root:
            Ruta a `runs/<experiment_id>/` (debe contener `instances/`).

        Retorna
        -------
        Lista de `ScannedRun`, uno por run logico (grupo de reintentos)
        encontrado. Orden estable: por `instance_id`, luego `agent_id`.
        """
        instances_dir = runs_root / "instances"
        if not instances_dir.is_dir():
            logger.warning("No existe %s; no hay runs que escanear.", instances_dir)
            return []

        scanned: list[ScannedRun] = []
        for instance_dir in sorted(p for p in instances_dir.iterdir() if p.is_dir()):
            for agent_dir in sorted(p for p in instance_dir.iterdir() if p.is_dir()):
                scanned.extend(
                    self._scan_agent_dir(
                        instance_id=instance_dir.name,
                        agent_id=agent_dir.name,
                        agent_dir=agent_dir,
                    )
                )
        return scanned

    def _scan_agent_dir(
        self, *, instance_id: str, agent_id: str, agent_dir: Path
    ) -> list[ScannedRun]:
        """Agrupa por raiz de hash y selecciona el canonico dentro de cada grupo."""
        groups: dict[str, list[tuple[int, Path]]] = {}
        for run_dir in sorted(p for p in agent_dir.iterdir() if p.is_dir()):
            if not (run_dir / "run_record.json").exists():
                logger.info("Ignorando %s: falta run_record.json.", run_dir)
                continue
            match = _RUN_DIR_RE.match(run_dir.name)
            if not match:
                logger.warning("Ignorando %s: nombre de carpeta no reconocido.", run_dir)
                continue
            seq = int(match.group("seq")) if match.group("seq") else 0
            groups.setdefault(match.group("hash"), []).append((seq, run_dir))

        results: list[ScannedRun] = []
        for hash_root in sorted(groups):
            entries = groups[hash_root]
            canonical_seq, canonical_dir = max(entries, key=lambda e: e[0])
            results.append(
                ScannedRun(
                    instance_id=instance_id,
                    agent_id=agent_id,
                    run_dir=canonical_dir,
                    run_sequence=canonical_seq,
                    retry_count=len(entries) - 1,
                )
            )
        return results
