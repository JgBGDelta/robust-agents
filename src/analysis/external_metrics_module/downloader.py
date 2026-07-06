"""Descarga y extraccion de archivos de releases externos.

Gestiona el ciclo completo: HTTP GET con reintentos y backoff exponencial,
verificacion de tamano, descompresion de ZIP si aplica, y cache local
idempotente.

La descarga aborta con excepcion explicita si se agotan los reintentos: este
modulo se ejecuta de forma manual/semi-manual una vez antes del analisis, no
en un bucle de 120 runs, por lo que no tiene sentido continuar con datos
parciales sin que el operador lo sepa.

Acoplamientos:
- Usado por `ExternalMetricsModule.ensure_downloaded()`.
"""

from __future__ import annotations

import io
import logging
import time
import urllib.request
import zipfile
from pathlib import Path

from analysis.external_metrics_module.external_metrics_config import ExternalMetricsConfig
from analysis.external_metrics_module.external_source_config import ExternalSourceConfig

logger = logging.getLogger(__name__)

# Nombre fijo del fichero de descarga raw dentro del directorio de cache.
_DOWNLOAD_FILENAME = "_download_raw"


class Downloader:
    """Descarga y descomprime el JSONL de predicciones de un agente externo.

    Idempotente: si el JSONL de destino ya existe y no esta vacio, no repite
    la descarga. Esto permite relanzar el pipeline sin coste de red.
    """

    def __init__(self, config: ExternalMetricsConfig) -> None:
        """
        Parametros
        ----------
        config:
            Configuracion operativa (reintentos, tamano maximo).
        """
        self._config = config

    def ensure_jsonl(self, source_config: ExternalSourceConfig) -> Path:
        """Garantiza que el JSONL esta disponible en ``local_cache_dir``.

        Si la URL apunta a un ZIP, lo descarga y extrae el miembro JSONL.
        Si la URL apunta directamente a un JSONL, lo descarga tal cual.
        El resultado se persiste en ``local_cache_dir / <source_id>.jsonl``.

        Parametros
        ----------
        source_config:
            Configuracion de la fuente externa.

        Retorna
        -------
        Path al fichero JSONL local listo para parsear.

        Lanza
        -----
        RuntimeError
            Si la descarga falla tras agotar todos los reintentos.
        """
        dest_jsonl = source_config.local_cache_dir / f"{source_config.source_id}.jsonl"

        if self._is_valid_jsonl(dest_jsonl):
            logger.info(
                "[%s] JSONL ya en cache: %s", source_config.source_id, dest_jsonl
            )
            return dest_jsonl

        source_config.local_cache_dir.mkdir(parents=True, exist_ok=True)
        raw_bytes = self._download_with_retries(
            source_config.release_url, source_config.source_id
        )

        if _is_zip(raw_bytes):
            jsonl_bytes = self._extract_jsonl_from_zip(
                raw_bytes, source_config.archive_member, source_config.source_id
            )
        else:
            # La URL ya apuntaba directamente a un JSONL.
            jsonl_bytes = raw_bytes

        dest_jsonl.write_bytes(jsonl_bytes)
        logger.info(
            "[%s] JSONL guardado: %s (%d bytes)",
            source_config.source_id,
            dest_jsonl,
            len(jsonl_bytes),
        )
        return dest_jsonl

    # ------------------------------------------------------------------
    # Metodos privados
    # ------------------------------------------------------------------

    def _download_with_retries(self, url: str, source_id: str) -> bytes:
        """Descarga ``url`` con hasta ``max_retries`` intentos.

        Espera 2^n segundos entre intentos (n = 1, 2, ...).
        Lanza ``RuntimeError`` si se agotan los intentos.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                logger.info(
                    "[%s] Descargando %s (intento %d/%d)",
                    source_id,
                    url,
                    attempt,
                    self._config.max_retries,
                )
                data = self._fetch(url)
                self._verify_size(data, url)
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < self._config.max_retries:
                    wait = 2**attempt
                    logger.warning(
                        "[%s] Intento %d fallido (%s). Reintentando en %ds.",
                        source_id,
                        attempt,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "[%s] Descarga fallida tras %d intentos: %s",
                        source_id,
                        self._config.max_retries,
                        exc,
                    )
        raise RuntimeError(
            f"[{source_id}] Descarga fallida tras {self._config.max_retries} intentos "
            f"({url}): {last_exc}"
        ) from last_exc

    def _fetch(self, url: str) -> bytes:
        """Realiza la peticion HTTP y devuelve el cuerpo como bytes."""
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
            return response.read()

    def _verify_size(self, data: bytes, url: str) -> None:
        """Rechaza descargas vacias o excesivamente grandes."""
        if len(data) == 0:
            raise ValueError(f"Descarga vacia desde {url}")
        if len(data) > self._config.max_download_bytes:
            raise ValueError(
                f"Descarga demasiado grande: {len(data)} bytes > "
                f"{self._config.max_download_bytes} bytes ({url})"
            )

    @staticmethod
    def _extract_jsonl_from_zip(
        raw_bytes: bytes,
        archive_member: str | None,
        source_id: str,
    ) -> bytes:
        """Extrae el JSONL del ZIP.

        Si ``archive_member`` esta especificado, extrae ese fichero
        exactamente. Si es ``None``, busca el primer ``.jsonl`` en el ZIP
        (busqueda recursiva por si el ZIP tiene subdirectorios).
        """
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            names = zf.namelist()

            if archive_member is not None:
                if archive_member not in names:
                    raise ValueError(
                        f"[{source_id}] '{archive_member}' no encontrado en el ZIP. "
                        f"Contenido: {names}"
                    )
                return zf.read(archive_member)

            # Busca automaticamente el primer .jsonl en el ZIP.
            jsonl_names = [n for n in names if n.endswith(".jsonl")]
            if not jsonl_names:
                raise ValueError(
                    f"[{source_id}] No se encontro ningun .jsonl en el ZIP. "
                    f"Contenido: {names}"
                )
            if len(jsonl_names) > 1:
                logger.warning(
                    "[%s] ZIP contiene varios .jsonl: %s. Usando el primero.",
                    source_id,
                    jsonl_names,
                )
            return zf.read(jsonl_names[0])

    @staticmethod
    def _is_valid_jsonl(path: Path) -> bool:
        """Comprueba que el fichero existe y tiene contenido."""
        return path.exists() and path.stat().st_size > 0


def _is_zip(data: bytes) -> bool:
    """Comprueba la firma magica de un fichero ZIP (primeros 4 bytes: PK\\x03\\x04)."""
    return len(data) >= 4 and data[:4] == b"PK\x03\x04"
