"""`AnalysisFrames`: contenedor de los `DataFrame` ya cargados y tipados.

Encapsula la lectura con `pandas.read_csv` y el tipado explicito de columnas
que `pandas` podria inferir de forma ambigua desde texto plano (booleanas con
huecos `NaN`, categoricas) para que ninguna funcion de figura tenga que
hacerlo por su cuenta (`modulo_diagramas.md` S6-S7).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Columnas booleanas de `datos_consolidados.csv` que `csv.DictWriter` serializa
# como texto "True"/"False"/"" (celda vacia = None/NaN). Sin tipado explicito,
# `pandas` las deja como `object` (cadenas), rompiendo comparaciones `== True`.
_RUN_BOOLEAN_COLUMNS = (
    "patch_available",
    "submitted",
    "empty_submission",
    "evaluable",
    "resolved",
)

# Columnas categoricas de `datos_consolidados.csv` (S6.1/S6.2 de la spec general).
_RUN_CATEGORICAL_COLUMNS = (
    "source_type",
    "sample_group",
    "run_status",
    "failure_category",
    "agent_id",
    "configuration_id",
)

_STEP_CATEGORICAL_COLUMNS = (
    "uncertainty_level",
    "structural_risk_level",
    "controller_action",
)


@dataclass
class AnalysisFrames:
    """`DataFrame` ya cargados y tipados, listos para las funciones de figura."""

    runs: pd.DataFrame
    steps: pd.DataFrame | None = None

    @classmethod
    def load(cls, csv_path: Path, steps_csv_path: Path | None = None) -> "AnalysisFrames":
        """Carga `datos_consolidados.csv` y, opcionalmente, `datos_pasos_consolidados.csv`.

        Parametros
        ----------
        csv_path:
            Ruta a `datos_consolidados.csv`.
        steps_csv_path:
            Ruta a `datos_pasos_consolidados.csv`; `None` si no se van a
            generar las figuras que lo requieren.

        Retorna
        -------
        `AnalysisFrames` con las columnas ambiguas ya tipadas.
        """
        runs = pd.read_csv(csv_path)
        runs = _type_boolean_columns(runs, _RUN_BOOLEAN_COLUMNS)
        runs = _type_categorical_columns(runs, _RUN_CATEGORICAL_COLUMNS)

        steps = None
        if steps_csv_path is not None:
            steps = pd.read_csv(steps_csv_path)
            steps = _type_categorical_columns(steps, _STEP_CATEGORICAL_COLUMNS)

        return cls(runs=runs, steps=steps)


def _type_boolean_columns(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Convierte columnas de texto "True"/"False" a booleano nulable de pandas."""
    for column in columns:
        if column not in df.columns:
            continue
        df[column] = df[column].map({"True": True, "False": False, True: True, False: False}).astype("boolean")
    return df


def _type_categorical_columns(df: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Convierte columnas de texto plano a `category`, preservando `NaN`."""
    for column in columns:
        if column not in df.columns:
            continue
        df[column] = df[column].astype("category")
    return df
