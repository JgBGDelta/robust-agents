# Sobre esta documentación

Esta página describe cómo está organizada la documentación del proyecto y qué
herramientas la generan. El sitio publicado está en
[GitHub Pages](https://jgbgdelta.github.io/robust-agents/).

## Herramientas

| Herramienta | Versión mínima | Función |
|-------------|----------------|---------|
| [MkDocs](https://www.mkdocs.org/) | 1.6 | Motor de construcción del sitio estático |
| [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) | 9.0 | Tema visual y extensiones de Markdown |
| [mkdocstrings](https://mkdocstrings.github.io/) | 0.27 | Extrae docstrings Python y los inserta como HTML |
| [mkdocstrings-python](https://mkdocstrings.github.io/python/) | - | Handler Python de mkdocstrings (Griffe) |

Las dependencias de documentación están declaradas en `pyproject.toml` bajo el extra `docs`:

```toml
[project.optional-dependencies]
docs = [
    "mkdocs-material >= 9.0",
    "mkdocstrings[python] >= 0.27",
]
```

## Estructura de ficheros

```
mkdocs.yml                      # Configuración del sitio (nav, plugins, tema)
README.md                       # Manual de usuario (fuente única; también portada en GitHub)
docs/
  manual-usuario.md             # Incluye README.md (sin duplicar contenido)
  sobre-la-documentacion.md     # Este fichero
  especificaciones/
    agente/                     # Especificaciones del bloque del agente
    benchmark/                  # Especificaciones del bloque del benchmark
    analisis/                   # Especificaciones del bloque de análisis
  api/
    index.md                    # Visión general de la implementación
    agent/                      # Código documentado de src/agent/
    benchmark/                  # Código documentado de src/benchmark/
    analysis/                   # Código documentado de src/analysis/
```

La documentación de **diseño** (guías y especificaciones) está en `docs/`.
La documentación de **código** (implementación) está en `docs/api/` y se
**genera automáticamente** a partir de los docstrings en `src/`.

## Cómo funciona la sección Implementación

Las páginas bajo `docs/api/` contienen directivas como:

```markdown
::: agent.robust_agent.RobustAgent
```

Al construir el sitio, mkdocstrings localiza la clase `RobustAgent` dentro de `src/`
(ruta configurada en `mkdocs.yml` → `plugins.mkdocstrings.handlers.python.paths`),
extrae su docstring y los de todos sus métodos públicos, y los renderiza como HTML.

Los docstrings del proyecto admiten **Markdown** (`` `identificadores` ``, listas,
bloques de código, etc.), que Material renderiza en la web.

Los diagramas Mermaid de las especificaciones se habilitan en `mkdocs.yml` mediante
`pymdownx.superfences` con un fence `mermaid`; Material los dibuja en el navegador al
cargar la página.

## Publicación en GitHub Pages

El sitio se publica en
[https://jgbgdelta.github.io/robust-agents/](https://jgbgdelta.github.io/robust-agents/)
mediante el workflow `.github/workflows/docs.yml`, que se dispara en cada `push` a `main`
y ejecuta `mkdocs gh-deploy` (rama `gh-pages`).

## Relación con la memoria del TFG

La documentación web de este repositorio y la memoria LaTeX del TFG son **complementarias**:

| | Documentación web | Memoria LaTeX |
|---|---|---|
| Audiencia | Tribunal y lectores vía URL | Tribunal (PDF) |
| Contenido | Especificaciones, implementación, manual de uso | Capítulos académicos completos |
| Generación | MkDocs + mkdocstrings | `latexmk` / LaTeX Workshop |
| Repositorio | [JgBGDelta/robust-agents](https://github.com/JgBGDelta/robust-agents) | [JgBGDelta/robust-agents-tfg-info](https://github.com/JgBGDelta/robust-agents-tfg-info) |

Los ficheros de especificación en `docs/especificaciones/` se redactaron como
documentos de trabajo durante el diseño del proyecto y se publican aquí tal cual.
El contenido académico elaborado, con contexto bibliográfico y análisis, se mantiene
en el repositorio de la memoria.
