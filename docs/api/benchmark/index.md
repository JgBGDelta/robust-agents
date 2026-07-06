# API del benchmark

Paquete `benchmark`: orquestación de experimentos, carga de dataset, ejecución de runs,
evaluación funcional y agregación de resultados.

## Exportaciones públicas

::: benchmark

## Páginas por componente

- [BenchmarkRunner](benchmark_runner.md) - punto de entrada del pipeline
- [Configuración](config.md) - `ExperimentConfig` y configs asociadas
- [Dataset](dataset.md) - carga y normalización de instancias
- [Ejecución](execution.md) - runs del agente en contenedor
- [Evaluación](evaluation.md) - submit a SWE-bench y métricas
- [Resultados](results.md) - layout, resume y exportación
