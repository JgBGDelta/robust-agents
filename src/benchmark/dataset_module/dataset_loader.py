"""Fachada sobre `datasets.load_dataset` con cache HuggingFace.

`modulo_dataset.md` MD.9 lo documenta como submodulo interno; aqui se
materializa con dos responsabilidades estrechas:

- envolver la llamada a HuggingFace para que el modulo de dataset
  trabaje con `list[dict]` plano (mas facil de mockear en tests),
- aceptar un `loader_fn` inyectado para permitir sustituir HuggingFace
  por una funcion fixture en tests unitarios sin tocar la red.

El paquete `datasets` se importa de forma perezosa: si el caller
inyecta `loader_fn`, el modulo de dataset funciona sin necesidad de
que `datasets` este instalado.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class DatasetLoaderProtocol(Protocol):
    """Contrato minimo de un cargador de dataset."""

    def load(self, *, name: str, subset: str, split: str) -> list[dict[str, Any]]:
        """Devuelve una lista de filas crudas del dataset solicitado."""
        ...


class DatasetLoader:
    """Cargador por defecto que llama a `datasets.load_dataset`.

    Si el caller inyecta `loader_fn`, esta fachada delega en ella sin
    importar `datasets`, lo que permite tests unitarios sin la
    dependencia.
    """

    def __init__(
        self,
        *,
        loader_fn: Callable[..., Any] | None = None,
    ) -> None:
        """Almacena el callable de carga (HF por defecto)."""
        self._loader_fn = loader_fn

    def load(self, *, name: str, subset: str, split: str) -> list[dict[str, Any]]:
        """Carga el dataset y devuelve sus filas como lista de diccionarios.

        El parametro `subset` se acepta por compatibilidad con la API
        publica del modulo (alias canonico: `lite`, `verified`, ...);
        HuggingFace usa el `name` completo, asi que el subset solo se
        propaga en el caso (raro) de que el caller haya inyectado un
        loader que lo necesite.
        """
        if self._loader_fn is not None:
            raw = self._loader_fn(name=name, subset=subset, split=split)
        else:
            raw = self._load_from_huggingface(name=name, split=split)
        return [dict(row) for row in raw]

    @staticmethod
    def _load_from_huggingface(*, name: str, split: str) -> Any:
        """Llama a `datasets.load_dataset` con cache local."""
        from datasets import load_dataset  # import perezoso: evita tocar HF en tests

        return load_dataset(name, split=split)
