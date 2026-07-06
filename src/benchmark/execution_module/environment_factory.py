"""`EnvironmentFactory` y verificacion de precondiciones (PR1).

`modulo_ejecucion.md` ME.5.1, EB.5.2. La fabrica encapsula la
construccion del entorno aislado (Docker por defecto, siguiendo la
convencion SWE-bench) y la limpieza al terminar el run. Es
**inyectable**: el caller puede pasar callables alternativos para
tests sin necesidad de Docker.

`PreconditionChecker.is_repo_clean(handle)` materializa PR1 (EB.5.2):
verifica que `cwd` es un repositorio git limpio. Por defecto invoca
`git status --porcelain` via `subprocess`; tests pueden inyectar un
`status_fn` que devuelva canned output.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from benchmark.config import AgentRunConfig
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance


@dataclass
class EnvironmentHandle:
    """Handle ligero al entorno aislado de un run.

    `cwd` es el directorio del repositorio sobre el que opera el
    agente. `raw_env` puede ser el objeto `Environment` del
    mini-SWE-agent (cuando se usa Docker) o `None` en tests con
    fakes.
    """

    cwd: Path
    raw_env: Any | None = None
    image_digest: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


BuildFn = Callable[[BenchmarkInstance, AgentRunConfig], EnvironmentHandle]
CleanupFn = Callable[[EnvironmentHandle], None]


SWEBENCH_WORKDIR = "/testbed"


def docker_image_for_instance(instance: BenchmarkInstance) -> str:
    """Devuelve la imagen Docker oficial de una instancia SWE-bench."""
    meta = instance.metadata or {}
    image = meta.get("image_name") or meta.get("docker_image")
    if image:
        return str(image)
    docker_id = instance.instance_id.replace("__", "_1776_")
    return f"docker.io/swebench/sweb.eval.x86_64.{docker_id}:latest".lower()


class EnvironmentFactory:
    """Construye y limpia entornos aislados por run.

    Por defecto delega en `mini-SWE-agent` (`DockerEnvironment`) via
    el `agent_run_config.environment_class`. Tests inyectan
    `build_fn`/`cleanup_fn` para evitar Docker.
    """

    def __init__(
        self,
        *,
        build_fn: BuildFn | None = None,
        cleanup_fn: CleanupFn | None = None,
    ) -> None:
        """Almacena las callables de construccion/limpieza inyectadas."""
        self._build_fn = build_fn
        self._cleanup_fn = cleanup_fn

    def build(
        self,
        *,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
    ) -> EnvironmentHandle:
        """Construye el entorno para un run."""
        if self._build_fn is not None:
            return self._build_fn(instance, agent_run_config)
        return self._build_docker(instance, agent_run_config)

    def _build_docker(
        self,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
    ) -> EnvironmentHandle:
        """Levanta el contenedor SWE-bench via mini-swe-agent."""
        from minisweagent.environments import get_environment_class

        image = docker_image_for_instance(instance)
        env_cls = get_environment_class(agent_run_config.environment_class)
        raw_env = env_cls(image=image, cwd=SWEBENCH_WORKDIR, timeout=60)
        return EnvironmentHandle(
            cwd=Path(SWEBENCH_WORKDIR),
            raw_env=raw_env,
            image_digest=None,
            metadata={"docker_image": image, "instance_id": instance.instance_id},
        )

    def cleanup(self, handle: EnvironmentHandle) -> None:
        """Libera recursos del entorno tras el run."""
        if self._cleanup_fn is not None:
            self._cleanup_fn(handle)
            return
        if handle.raw_env is not None and hasattr(handle.raw_env, "cleanup"):
            handle.raw_env.cleanup()


# --- PR1: precondicion de repositorio limpio -----------------------------------


StatusFn = Callable[[Path], tuple[int, str, str]]
"""Firma de un runner de `git status --porcelain` inyectable.

Devuelve `(returncode, stdout, stderr)` para que el checker pueda
distinguir entre "repo limpio" y "repo no es git".
"""


class PreconditionChecker:
    """Verifica PR1 antes de invocar al agente (`modulo_ejecucion.md` ME.5.2)."""

    @staticmethod
    def is_repo_clean(
        handle: EnvironmentHandle,
        *,
        status_fn: StatusFn | None = None,
    ) -> tuple[bool, str | None]:
        """Devuelve `(clean, reason)` aplicando `git status --porcelain`."""
        if status_fn is not None:
            returncode, stdout, stderr = status_fn(handle.cwd)
        elif handle.raw_env is not None and hasattr(handle.raw_env, "execute"):
            returncode, stdout, stderr = PreconditionChecker._git_status_via_environment(handle)
        else:
            returncode, stdout, stderr = PreconditionChecker._run_git_status(handle.cwd)
        if returncode != 0:
            return False, f"git_status_returncode={returncode}: {stderr.strip() or stdout.strip()}"
        if stdout.strip():
            return False, f"unclean_repo: {stdout.strip()[:200]}"
        return True, None

    @staticmethod
    def _git_status_via_environment(handle: EnvironmentHandle) -> tuple[int, str, str]:
        """Ejecuta `git status --porcelain` dentro del entorno aislado (p. ej. Docker)."""
        result = handle.raw_env.execute(
            {"command": "git status --porcelain"},
            cwd=handle.cwd.as_posix(),
        )
        return (
            int(result.get("returncode", -1)),
            str(result.get("output", "")),
            str(result.get("exception_info", "")),
        )

    @staticmethod
    def _run_git_status(cwd: Path) -> tuple[int, str, str]:
        """Auxiliar interno: run git status."""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr


# --- Helper para tests: levanta un repo git temporal limpio ---------------------


def make_clean_temp_repo(*, message: str = "init") -> Path:
    """Crea un repositorio git temporal con un commit inicial limpio.

    Helper destinado a fixtures de test; el modulo lo expone para que
    los tests no tengan que reimplementar la secuencia
    `git init` + commit. Devuelve la ruta del repo (cleanup queda a
    cargo del caller via `tempfile`/`tmp_path`).
    """
    cwd = Path(tempfile.mkdtemp(prefix="bench_repo_"))
    subprocess.run(["git", "init", "-q"], cwd=str(cwd), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(cwd), check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(cwd), check=True)
    (cwd / "README.md").write_text("# test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(cwd), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=str(cwd),
        check=True,
    )
    return cwd


def cleanup_temp_repo(path: Path) -> None:
    """Elimina un repo creado con `make_clean_temp_repo`."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
