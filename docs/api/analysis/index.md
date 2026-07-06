# API del análisis

Paquete `analysis` (Bloque 3): consolidación tabular de runs, métricas de agentes
externos y generación de diagramas a partir de los CSV producidos.

## Módulos públicos

::: analysis.consolidation_module

::: analysis.external_metrics_module

::: analysis.diagrams_module

## Páginas por componente

- [Consolidación](consolidation.md) - `ConsolidationModule`, filas CSV
- [Métricas externas](external_metrics.md) - `ExternalMetricsModule`, fuentes precomputadas
- [Diagramas](diagrams.md) - `DiagramsModule`, figuras matplotlib
- [Punto de entrada CLI](run.md) - `python -m analysis.run`
