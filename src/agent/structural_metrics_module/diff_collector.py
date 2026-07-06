"""Recolector de diffs para el modulo de metricas estructurales."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _GitResult:
    """Resultado normalizado de un comando git."""

    stdout: str
    stderr: str
    returncode: int


class DiffCollector:
    """Materializa referencias de paso y devuelve diffs incremental/acumulado.

    Si se inyecta un ``env`` con ``execute`` (p. ej. ``DockerEnvironment``),
    los comandos git se ejecutan **dentro** del entorno aislado usando ``cwd``
    lógico del repositorio (``/testbed`` en SWE-bench). Sin ``env``, usa
    ``subprocess`` en el host (tests locales con repo git real en disco).
    """

    def __init__(self, repo_path: Path, *, env: Any | None = None) -> None:
        """Inicializa el recolector para un repositorio git."""
        self.repo_path = repo_path
        self._git_cwd = repo_path.as_posix()
        self._env = env if env is not None and hasattr(env, "execute") else None

    def ensure_pr1(self) -> None:
        """Valida PR1: repo git valido al inicio.

        El arbol puede estar sucio si la imagen SWE-bench tiene ficheros
        pre-modificados por configuracion del entorno (p. ej. setup.py,
        tox.ini). Esto es esperado en algunas imagenes oficiales y no
        impide que el agente funcione correctamente. Solo verificamos que
        estamos dentro de un repo git valido.
        """
        self._run_git("rev-parse --is-inside-work-tree")

    def baseline_ref(self) -> str:
        """Devuelve la referencia base del run.

        Si el repo esta limpio, retorna HEAD. Si ya hay cambios pre-existentes
        (p. ej. imagenes SWE-bench con setup.py o tox.ini modificados), usa
        ``git stash create`` para materializar un ref que captura exactamente
        ese estado inicial. Asi los diffs de cada paso reflejan SOLO los cambios
        que introduce el agente, no los pre-existentes de la imagen.
        """
        dirty = self._run_git("status --porcelain").stdout.strip()
        if dirty:
            try:
                stash_ref = self._run_git("stash create").stdout.strip()
                if stash_ref:
                    return stash_ref
            except RuntimeError:
                pass
        return self._run_git("rev-parse HEAD").stdout.strip()

    def materialize_step_ref(self) -> str:
        """Devuelve una referencia estable del estado actual del workspace.

        Usa `git stash create` para capturar staged/unstaged sin modificar
        el arbol de trabajo. Si no hay cambios (o si la versión de git
        devuelve rc=1 con stdout vacío, comportamiento en Docker/SWE-bench),
        reutiliza `HEAD`.
        """
        try:
            stash_ref = self._run_git("stash create").stdout.strip()
        except RuntimeError:
            # Algunas versiones de git (p. ej. las del imagen SWE-bench)
            # devuelven rc=1 cuando no hay nada que guardar. Se trata como
            # «árbol limpio» y se reutiliza HEAD.
            stash_ref = ""
        if stash_ref:
            return stash_ref
        return self._run_git("rev-parse HEAD").stdout.strip()

    def get_cumulative_patch(self, initial_ref: str) -> str:
        """Devuelve el diff acumulado (texto) desde ``initial_ref`` hasta el estado actual.

        Utilizado por ``RobustAgent._apply_decision`` para obtener el parche
        real cuando el controlador decide ``FINALIZE(submit)``.
        Devuelve cadena vacía ante cualquier error para no bloquear la salida.
        """
        try:
            current_ref = self.materialize_step_ref()
            return self._run_git(f"diff {initial_ref}..{current_ref}").stdout
        except Exception:  # noqa: BLE001
            return ""

    def collect(self, previous_ref: str, current_ref: str, initial_ref: str) -> dict[str, Any]:
        """Calcula diffs incremental y acumulado y devuelve metadatos parseados."""
        incremental = self._run_git(f"diff --numstat {previous_ref}..{current_ref}").stdout
        cumulative = self._run_git(f"diff --numstat {initial_ref}..{current_ref}").stdout
        names = self._run_git(f"diff --name-only {previous_ref}..{current_ref}").stdout
        ext_counts = self._extension_counts(names.splitlines())
        hunks = self._run_git(f"diff -U0 {previous_ref}..{current_ref}").stdout
        cumulative_hunks = self._run_git(f"diff -U0 {initial_ref}..{current_ref}").stdout
        return {
            "incremental_numstat": incremental,
            "cumulative_numstat": cumulative,
            "files_changed": [line.strip() for line in names.splitlines() if line.strip()],
            "extension_counts": ext_counts,
            "hunks_patch": hunks,
            "cumulative_hunks_patch": cumulative_hunks,
        }

    def tracked_files_count(self) -> int:
        """Cuenta ficheros versionados para normalizar `files_changed_ratio`."""
        out = self._run_git("ls-files").stdout
        files = [line for line in out.splitlines() if line.strip()]
        return len(files)

    def _run_git(self, args: str) -> _GitResult:
        """Ejecuta un comando git y lanza error explicito si falla."""
        if self._env is not None:
            result = self._run_git_via_environment(args)
        else:
            result = self._run_git_via_subprocess(args)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Fallo git ({args}): {detail}")
        return result

    def _run_git_via_environment(self, args: str) -> _GitResult:
        """Ejecuta git via ``Environment.execute`` (Docker, local, etc.)."""
        completed = self._env.execute(
            {"command": f"git {args}"},
            cwd=self._git_cwd,
        )
        return _GitResult(
            stdout=str(completed.get("output", "")),
            stderr=str(completed.get("exception_info", "")),
            returncode=int(completed.get("returncode", -1)),
        )

    def _run_git_via_subprocess(self, args: str) -> _GitResult:
        """Ejecuta git en el host con ``subprocess`` (repos locales en tests)."""
        completed = subprocess.run(
            f"git {args}",
            cwd=self.repo_path,
            shell=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return _GitResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def _extension_counts(self, files: list[str]) -> dict[str, int]:
        """Agrupa ficheros por extension para el resumen de diff."""
        counts: dict[str, int] = {}
        for file in files:
            suffix = Path(file).suffix.lower() or "<sin_extension>"
            counts[suffix] = counts.get(suffix, 0) + 1
        return counts
