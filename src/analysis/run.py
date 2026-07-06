"""Punto de entrada CLI del Bloque 3 — pipeline de analisis.

Ejecuta en orden los tres modulos del sistema de analisis:
  1. (Opcional) Extraccion externa: ya producida por una ejecucion previa de
     ``ExternalMetricsModule``; este modulo solo consume los JSONL ya presentes en
     ``data/external_predictions/<source_id>/extracted_metrics.jsonl``.
  2. Consolidacion: ``ConsolidationModule`` sobre el arbol ``runs/<experiment_id>/``.
  3. Diagramas: ``DiagramsModule`` sobre los CSV producidos en el paso anterior.

Uso tipico:

    python -m analysis.run \\
        --runs-root runs/full-lite-120inst-6agents \\
        --output-dir data/full-lite-120inst-6agents \\
        --external data/external_predictions/agentless_v1.5/extracted_metrics.jsonl \\
        --external data/external_predictions/aider_20240523/extracted_metrics.jsonl \\
        --external data/external_predictions/moatless_claude35_20241117/extracted_metrics.jsonl \\
        --external data/external_predictions/openhands_claude35_20240725/extracted_metrics.jsonl

El script es **idempotente**: relanzarlo sobre el mismo estado en disco regenera
los CSV y las figuras desde cero. Para omitir la generacion de figuras (solo CSV):
usar ``--no-diagrams``.

Equivale al anterior ``scripts/run_analysis.py`` (eliminado en favor de este
patron coherente con ``python -m benchmark.run`` y ``python -m agent.cli``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("analysis.run")


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la CLI. Devuelve el exit code (0 = OK, 2 = figuras fallidas)."""
    parser = argparse.ArgumentParser(
        description="Pipeline de analisis del Bloque 3: consolidacion + diagramas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--runs-root",
        required=True,
        type=Path,
        metavar="PATH",
        help="Ruta a runs/<experiment_id>/ (el arbol de resultados del Bloque 2).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        metavar="PATH",
        help="Directorio de salida para datos_consolidados.csv y figuras/.",
    )
    parser.add_argument(
        "--external",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        dest="external_paths",
        help=(
            "Ruta a un extracted_metrics.jsonl producido por ExternalMetricsModule. "
            "Puede repetirse para incluir multiples fuentes externas."
        ),
    )
    parser.add_argument(
        "--no-diagrams",
        action="store_true",
        help="Omite la generacion de figuras; solo produce los CSV.",
    )
    parser.add_argument(
        "--minimal-only",
        action="store_true",
        help="Genera solo el catalogo minimo (S8.1) en lugar del catalogo completo.",
    )
    args = parser.parse_args(argv)

    if not args.runs_root.exists():
        logger.error("--runs-root no existe: %s", args.runs_root)
        return 1

    missing_external = [p for p in args.external_paths if not p.exists()]
    if missing_external:
        logger.error("Ficheros externos no encontrados: %s", missing_external)
        return 1

    # -- Consolidacion ----------------------------------------------------------
    from analysis.consolidation_module import ConsolidationModule

    logger.info("=== Consolidacion ===")
    module = ConsolidationModule()
    run_rows, step_rows = module.consolidate(
        runs_root=args.runs_root,
        external_metrics_paths=args.external_paths or [],
    )
    logger.info(
        "Consolidados %d runs (%d propios, %d externos) y %d pasos.",
        len(run_rows),
        sum(1 for r in run_rows if r.source_type == "own"),
        sum(1 for r in run_rows if r.source_type == "external"),
        len(step_rows),
    )

    csv_path, steps_csv_path = module.write_csv(run_rows, step_rows, args.output_dir)
    logger.info("CSV escritos en %s", args.output_dir)

    if args.no_diagrams:
        logger.info("--no-diagrams: omitiendo generacion de figuras.")
        return 0

    # -- Diagramas --------------------------------------------------------------
    from analysis.diagrams_module import DiagramsModule

    logger.info("=== Diagramas ===")
    diag = DiagramsModule()
    frames = diag.load_data(csv_path, steps_csv_path)

    figures_dir = args.output_dir / "figures"
    if args.minimal_only:
        report = diag.generate_minimal(frames, figures_dir)
    else:
        report = diag.generate_all(frames, figures_dir)

    logger.info("Figuras generadas: %d", len(report.generated))
    if report.skipped:
        logger.warning(
            "Figuras omitidas por datos insuficientes (%d): %s",
            len(report.skipped),
            list(report.skipped.keys()),
        )
    if report.failed:
        logger.error(
            "Figuras con error inesperado (%d): %s",
            len(report.failed),
            list(report.failed.keys()),
        )
    for path in report.generated:
        logger.info("  %s", path.name)

    return 2 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
