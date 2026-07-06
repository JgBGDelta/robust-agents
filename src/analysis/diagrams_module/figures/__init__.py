"""Funciones de figura del catalogo minimo (S8.1 de `especificacion_analisis.md`).

Cada modulo define una unica funcion pura `plot_<nombre>(frames, output_path)`,
registrada en `FIGURE_REGISTRY` (`diagrams_module.py`). Ninguna funcion importa
otra funcion de figura; su unica dependencia compartida es `analysis.diagrams_module.style`.
"""
