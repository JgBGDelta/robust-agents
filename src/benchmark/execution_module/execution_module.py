"""`BenchmarkExecutionModule`: orquestador del modulo de ejecucion.

Encadena:
- construccion de la lista experimental (`build_run_list`),
- deteccion de runs ya ejecutados (`BenchmarkResultsModule.pending_runs`),
- ciclo de vida de cada intento (`RunExecutor`),
- politica de reintentos (`RetryPolicy`).

`build_run_list` y `run_id` generan la lista cartesiana de runs; el
`group_id` agrupa por `(agent_id, model_id)` sin segmento de perfil en rutas.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from benchmark.config import AgentRunConfig, BenchmarkConfig, DatasetConfig, ExperimentConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.dataset_module.slice_selector import SliceSelector
from benchmark.execution_module.agent_factory import AgentFactory
from benchmark.execution_module.benchmark_run_record import BenchmarkRunRecord
from benchmark.execution_module.environment_factory import (
    EnvironmentFactory,
    StatusFn,
    docker_image_for_instance,
)
from benchmark.execution_module.retry_policy import RetryPolicy
from benchmark.execution_module.run_executor import ClockFn, RunExecutor
from benchmark.results_module.contracts import PlannedRun
from benchmark.results_module.layout import path_safe
from benchmark.shutdown import request_stop, stop_requested

if TYPE_CHECKING:
    from benchmark.run_progress import RunProgress


class BenchmarkExecutionModule:
    """Harness de ejecucion del benchmark."""

    def __init__(
        self,
        config: BenchmarkConfig,
        results_module: Any,
        *,
        environment_factory: EnvironmentFactory | None = None,
        agent_factory: AgentFactory | None = None,
        pr1_status_fn: StatusFn | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        """Inicializa `BenchmarkExecutionModule`."""
        self._config = config
        self._results_module = results_module
        self._environment_factory = environment_factory or EnvironmentFactory()
        self._agent_factory = agent_factory or AgentFactory(
            mini_config_path=config.mini_agent_config
        )
        self._pr1_status_fn = pr1_status_fn
        self._clock = clock
        self._retry_policy = RetryPolicy(
            max_retries=int(config.retry_policy.get("max_retries", 1))
        )

    # -- API publica ---------------------------------------------------------

    @staticmethod
    def build_run_list(
        experiment_config: ExperimentConfig,
        instances: list[BenchmarkInstance],
    ) -> list[PlannedRun]:
        """Devuelve la lista experimental `instances × agents` ordenada por instancia.

        Si un agente define ``dataset_slice_size``, solo se incluye en las
        instancias del sub-slice correspondiente (misma ``slice_strategy`` y
        ``slice_seed`` que el pool global, pero con el tamaño reducido).
        """
        # Pre-computar sets de instancias válidas por agente con sub-slice.
        agent_allowed: dict[str, set[str]] = {}
        for agent_run_config in experiment_config.agents:
            if agent_run_config.dataset_slice_size is not None:
                override_cfg = DatasetConfig(
                    slice_size=agent_run_config.dataset_slice_size,
                    slice_seed=experiment_config.dataset.slice_seed,
                    slice_strategy=experiment_config.dataset.slice_strategy,
                )
                sliced = SliceSelector.select(instances, override_cfg)
                agent_allowed[agent_run_config.agent_id] = {
                    inst.instance_id for inst in sliced
                }

        cells: list[PlannedRun] = []
        for instance in instances:
            for agent_run_config in experiment_config.agents:
                allowed = agent_allowed.get(agent_run_config.agent_id)
                if allowed is not None and instance.instance_id not in allowed:
                    continue
                cells.append(
                    PlannedRun(
                        instance_id=instance.instance_id,
                        agent_run_config=agent_run_config,
                        run_id=BenchmarkExecutionModule.run_id(
                            instance_id=instance.instance_id,
                            agent_run_config=agent_run_config,
                        ),
                    )
                )
        return cells

    @staticmethod
    def run_id(*, instance_id: str, agent_run_config: AgentRunConfig) -> str:
        """Devuelve el `run_id` determinista del run base.

        Clave: `<instance_id>|<agent_id>|<model_id>|<seed>` (sin perfil).
        Formato: `r-<8 hex chars>`.
        """
        key = "|".join(
            [
                instance_id,
                agent_run_config.agent_id,
                agent_run_config.model_id,
                "" if agent_run_config.seed is None else str(agent_run_config.seed),
            ]
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
        return path_safe(f"r-{digest}")

    @staticmethod
    def retry_run_id(base_run_id: str, *, attempt: int) -> str:
        """Construye el `run_id` de un reintento a partir del `run_id` base."""
        return f"{base_run_id}-r{int(attempt)}"

    def run_matrix(
        self,
        experiment_config: ExperimentConfig,
        instances: list[BenchmarkInstance],
        *,
        progress: "RunProgress | None" = None,
    ) -> list[BenchmarkRunRecord]:
        """Filtra los pendientes y ejecuta la lista experimental."""
        run_list = self.build_run_list(experiment_config, instances)
        pending = self._results_module.pending_runs(run_list)
        if not pending:
            return []

        # Repos con contextos muy grandes se ejecutan al final para minimizar
        # el impacto de sus picos de TPM sobre el resto del experimento.
        _HEAVY_REPOS = frozenset({"matplotlib__matplotlib"})
        pending = sorted(
            pending,
            key=lambda s: (1 if any(s.instance_id.startswith(r) for r in _HEAVY_REPOS) else 0),
        )

        self._cleanup_orphaned_containers()

        instance_index = {inst.instance_id: inst for inst in instances}
        pending_instance_ids = {slot.instance_id for slot in pending}
        self._cleanup_stale_images(pending_instance_ids)

        workers = max(1, int(self._config.workers))

        if workers == 1:
            return self._run_sequential(pending, instance_index, progress=progress)
        return self._run_parallel(pending, instance_index, workers=workers, progress=progress)

    def run_one(
        self,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
    ) -> BenchmarkRunRecord:
        """Ejecuta un run completo aplicando la politica de retries."""
        base_run_id = self.run_id(
            instance_id=instance.instance_id, agent_run_config=agent_run_config
        )
        return self._run_with_retries(
            instance=instance,
            agent_run_config=agent_run_config,
            base_run_id=base_run_id,
            worker_id=0,
        )

    # -- Ejecucion interna ---------------------------------------------------

    # Parametros del backoff dinamico por RateLimitError (429)
    _DELAY_STEP_UP: int = 15    # segundos que se añaden cuando hay reintentos por 429
    _DELAY_STEP_DOWN: int = 5   # segundos que se restan cuando el run va limpio
    _DELAY_MAX: int = 60        # techo absoluto del delay inter-run

    # ------------------------------------------------------------------ #
    # Limpieza de recursos Docker                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _cleanup_orphaned_containers() -> None:
        """Mata contenedores minisweagent- huerfanos de sesiones anteriores.

        Con workers=1 solo deberia existir un contenedor activo a la vez.
        Los Ctrl+C matan Python antes de que ``_safe_cleanup`` se ejecute,
        dejando contenedores corriendo que consumen RAM y CPU.
        """
        try:
            result = subprocess.run(
                ["docker", "ps", "-q", "--filter", "name=minisweagent-"],
                capture_output=True, text=True, timeout=10,
            )
            ids = [c for c in result.stdout.strip().splitlines() if c]
            if ids:
                subprocess.run(["docker", "kill"] + ids, capture_output=True, timeout=30)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _remove_docker_image(image: str) -> None:
        """Elimina una imagen Docker local para liberar espacio en disco.

        Se llama cuando todos los agentes de una instancia han sido procesados.
        Las imagenes SWE-bench pesan 3-8 GB cada una; con 120 instancias
        distintas pueden acumularse cientos de GB si no se limpian.
        """
        try:
            subprocess.run(
                ["docker", "rmi", image],
                capture_output=True, text=True, timeout=60,
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _cleanup_stale_images(pending_instance_ids: set[str]) -> None:
        """Elimina imagenes SWE-bench de instancias que ya no tienen runs pendientes.

        Al reanudar una sesion tras un corte, las instancias ya completadas
        en sesiones anteriores siguen teniendo sus imagenes en disco aunque
        el bucle secuencial nunca vuelva a pasar por ellas. Este metodo las
        detecta y las borra al inicio de cada sesion.

        La imagen de una instancia se conserva si su instance_id aparece en
        ``pending_instance_ids``; de lo contrario se elimina.
        """
        try:
            result = subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                capture_output=True, text=True, timeout=15,
            )
            for image in result.stdout.splitlines():
                image = image.strip()
                if "sweb.eval.x86_64." not in image:
                    continue
                # Extraer instance_id desde el nombre de imagen:
                # docker.io/swebench/sweb.eval.x86_64.{docker_id}:latest
                # docker_id = instance_id.replace("__", "_1776_")
                try:
                    docker_id = image.split("sweb.eval.x86_64.")[1].split(":")[0]
                    instance_id = docker_id.replace("_1776_", "__")
                except (IndexError, ValueError):
                    continue
                if instance_id not in pending_instance_ids:
                    subprocess.run(
                        ["docker", "rmi", image],
                        capture_output=True, text=True, timeout=60,
                    )
        except Exception:  # noqa: BLE001
            pass

    def _check_disk_space(self, instance_id: str) -> None:
        """Comprueba espacio libre antes de arrancar una instancia Docker.

        Si el disco donde reside el almacenamiento Docker tiene menos espacio
        libre que ``BenchmarkConfig.disk_min_free_gb``, activa la parada
        cooperativa y lanza ``KeyboardInterrupt`` para detener el experimento
        de forma ordenada, evitando fallos en cadena por disco lleno.

        La comprobacion se hace sobre el directorio temporal del sistema
        (``tempfile.gettempdir()``): en Windows con Docker Desktop/WSL2 ese
        directorio esta en C:, el mismo disco que aloja el VHDX. En Linux
        apunta a ``/tmp``, que suele estar en el mismo FS que ``/var/lib/docker``.

        Si ``disk_min_free_gb <= 0`` la comprobacion esta deshabilitada.
        Si ``shutil.disk_usage`` falla (permisos, plataforma), el error se
        ignora silenciosamente para no bloquear el experimento.
        """
        min_gb = self._config.disk_min_free_gb
        if min_gb <= 0:
            return
        try:
            check_path = tempfile.gettempdir()
            usage = shutil.disk_usage(check_path)
            free_gb = usage.free / (1024 ** 3)
        except Exception:  # noqa: BLE001
            return
        if free_gb < min_gb:
            msg = (
                f"[benchmark] Espacio libre insuficiente antes de instancia "
                f"'{instance_id}': {free_gb:.1f} GB libres < {min_gb:.1f} GB "
                f"requeridos. Deteniendo experimento para evitar fallos en "
                f"cadena. Libera espacio (docker system prune -a, compactar "
                f"VHDX) y reanuda con: python -m benchmark.run --config <cfg>"
            )
            logger.error(msg)
            print(f"\n{msg}\n", flush=True)
            request_stop()
            raise KeyboardInterrupt("disk_space_exhausted")

    def _run_sequential(
        self,
        pending: list[Any],
        instance_index: dict[str, BenchmarkInstance],
        *,
        progress: "RunProgress | None" = None,
    ) -> list[BenchmarkRunRecord]:
        """Ejecuta los runs en serie con delay dinamico anti-429.

        El delay empieza en ``inter_run_delay_seconds`` (tipicamente 0).
        Si un run tuvo reintentos por RateLimitError (429), el delay sube
        ``_DELAY_STEP_UP`` segundos (max ``_DELAY_MAX``). Si el run fue
        limpio, baja ``_DELAY_STEP_DOWN`` segundos (min 0). Asi el delay
        se auto-ajusta sin coste cuando no hay rate-limiting y reacciona
        rapidamente cuando la API empieza a limitar.
        """
        records: list[BenchmarkRunRecord] = []
        current_delay = max(0, int(self._config.inter_run_delay_seconds))
        prev_instance_id: str | None = None
        prev_instance_image: str | None = None
        for i, slot in enumerate(pending):
            if stop_requested():
                raise KeyboardInterrupt("benchmark_stop_requested")
            instance = instance_index.get(slot.instance_id)
            if instance is None:
                continue

            # Detectar cambio de instancia (incluida la primera iteracion).
            is_new_instance = slot.instance_id != prev_instance_id

            # Al cambiar de instancia todos los agentes de la anterior han
            # terminado: eliminar su imagen Docker para liberar disco.
            if is_new_instance and prev_instance_id is not None:
                if prev_instance_image:
                    self._remove_docker_image(prev_instance_image)
            prev_instance_id = slot.instance_id
            prev_instance_image = docker_image_for_instance(instance)

            # Verificar espacio libre antes de descargar/usar la imagen de la
            # nueva instancia. Se comprueba solo una vez por instance_id (no
            # por cada agente que comparte la misma imagen).
            if is_new_instance:
                self._check_disk_space(slot.instance_id)

            if current_delay > 0 and i > 0:
                self._interruptible_sleep(current_delay)
            if progress is not None:
                progress.on_run_start(i, slot.instance_id, slot.agent_run_config.agent_id)
            try:
                record = self._run_with_retries(
                    instance=instance,
                    agent_run_config=slot.agent_run_config,
                    base_run_id=slot.run_id,
                    worker_id=0,
                )
            except (KeyboardInterrupt, SystemExit):
                request_stop()
                raise
            if record.had_rate_limit_retries:
                current_delay = min(current_delay + self._DELAY_STEP_UP, self._DELAY_MAX)
            else:
                current_delay = max(current_delay - self._DELAY_STEP_DOWN, 0)
            if progress is not None:
                progress.on_run_complete(record)
            records.append(record)

        # Eliminar imagen de la ultima instancia procesada en la sesion.
        if prev_instance_image:
            self._remove_docker_image(prev_instance_image)

        return records

    def _run_parallel(
        self,
        pending: list[Any],
        instance_index: dict[str, BenchmarkInstance],
        *,
        workers: int,
        progress: "RunProgress | None" = None,
    ) -> list[BenchmarkRunRecord]:
        """Ejecuta runs en paralelo manteniendo como maximo ``workers`` activos.

        A diferencia de encolar todos los pendientes de golpe, aqui se usa un
        pipeline: cuando termina un run se encola el siguiente. Asi Ctrl+C
        detiene el experimento sin dejar cientos de tareas en cola.
        """
        records: list[BenchmarkRunRecord] = []

        def _execute_slot(
            session_index: int,
            slot: Any,
            *,
            worker_id: int,
        ) -> BenchmarkRunRecord:
            """Auxiliar interno: execute slot."""
            if stop_requested():
                raise KeyboardInterrupt("benchmark_stop_requested")
            instance = instance_index.get(slot.instance_id)
            if instance is None:
                raise ValueError(f"Instancia no encontrada: {slot.instance_id}")
            # En modo paralelo cada worker comprueba espacio independientemente
            # antes de iniciar su run. Si el disco esta lleno la comprobacion
            # activa stop_requested() y propaga KeyboardInterrupt, lo que hace
            # que el bucle principal del ThreadPoolExecutor aborte el resto.
            self._check_disk_space(slot.instance_id)
            if progress is not None:
                progress.on_run_start(
                    session_index, slot.instance_id, slot.agent_run_config.agent_id
                )
            return self._run_with_retries(
                instance=instance,
                agent_run_config=slot.agent_run_config,
                base_run_id=slot.run_id,
                worker_id=worker_id,
            )

        slots: list[tuple[int, Any, int]] = []
        for session_index, slot in enumerate(pending):
            instance = instance_index.get(slot.instance_id)
            if instance is None:
                continue
            slots.append((session_index, slot, len(slots) % workers))

        if not slots:
            return records

        pool = ThreadPoolExecutor(max_workers=workers)
        futures: dict[Any, int] = {}
        slot_iter = iter(slots)

        def _submit_next() -> None:
            """Auxiliar interno: submit next."""
            if stop_requested():
                return
            try:
                session_index, slot, worker_id = next(slot_iter)
            except StopIteration:
                return
            future = pool.submit(
                _execute_slot,
                session_index,
                slot,
                worker_id=worker_id,
            )
            futures[future] = session_index

        try:
            for _ in range(min(workers, len(slots))):
                _submit_next()

            while futures:
                if stop_requested():
                    raise KeyboardInterrupt("benchmark_stop_requested")
                # timeout=0.5: el hilo principal despierta cada 500ms para
                # comprobar stop_requested() aunque ningun future haya terminado.
                # Sin timeout, wait() bloquea en C y Ctrl+C no interrumpe en Windows.
                done, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    futures.pop(future, None)
                    try:
                        record = future.result()
                    except (KeyboardInterrupt, SystemExit):
                        request_stop()
                        raise
                    if progress is not None:
                        progress.on_run_complete(record)
                    records.append(record)
                    _submit_next()
        except (KeyboardInterrupt, SystemExit):
            request_stop()
            for future in list(futures):
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        return records

    @staticmethod
    def _interruptible_sleep(seconds: float) -> None:
        """Duerme en tramos cortos para que Ctrl+C responda rapido."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if stop_requested():
                raise KeyboardInterrupt("benchmark_stop_requested")
            time.sleep(min(1.0, end - time.monotonic()))

    def _run_with_retries(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        base_run_id: str,
        worker_id: int,
    ) -> BenchmarkRunRecord:
        """Auxiliar interno: run with retries."""
        executor = self._build_executor()
        attempts_done = 0
        run_id = base_run_id
        retry_of: str | None = None
        while True:
            if stop_requested():
                raise KeyboardInterrupt("benchmark_stop_requested")
            try:
                record = executor.execute(
                    instance=instance,
                    agent_run_config=agent_run_config,
                    run_id=run_id,
                    worker_id=worker_id,
                    retry_of=retry_of,
                )
            except (KeyboardInterrupt, SystemExit):
                # execute() ya persisto el record del run interrumpido.
                # Propagar para detener el bucle del experimento completo.
                request_stop()
                raise
            self._results_module.persist_run_record(record)

            if record.status != "failed":
                return record

            attempts_done += 1
            decision = self._retry_policy.decide_from_error(
                error=record.error,
                attempts_done=attempts_done,
            )
            if not decision.should_retry:
                return record
            retry_of = base_run_id if retry_of is None else retry_of
            run_id = self.retry_run_id(base_run_id, attempt=attempts_done)

    def _build_executor(self) -> RunExecutor:
        """Auxiliar interno: build executor."""
        return RunExecutor(
            results_module=self._results_module,
            environment_factory=self._environment_factory,
            agent_factory=self._agent_factory,
            pr1_status_fn=self._pr1_status_fn,
            clock=self._clock,
        )
