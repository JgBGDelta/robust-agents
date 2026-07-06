"""Fabrica de entornos locales con soporte bash en Windows.

`LocalEnvironment` del vendor usa `subprocess.run(..., shell=True)`, que en
Windows invoca `cmd.exe`. Los prompts del agente asumen bash (`ls`, `cat`, etc.).
`BashLocalEnvironment` ejecuta comandos via `bash -lc`, alineado con
`DockerEnvironment` del benchmark.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

ShellChoice = Literal["auto", "bash", "cmd"]
ResolvedShell = Literal["bash", "cmd"]

_GIT_BASH_CANDIDATES = (
    Path(r"C:\Program Files\Git\bin\bash.exe"),
    Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    Path(r"C:\Program Files (x86)\Git\usr\bin\bash.exe"),
)

# Stub de WSL en System32: `which bash` lo encuentra antes que Git Bash pero falla
# si WSL no esta instalado/configurado.
_WSL_BASH_STUB_DIRS = ("system32", "syswow64")


def _bash_is_usable(bash_path: str | Path) -> bool:
    """Comprueba que el ejecutable responde a `bash -lc`."""
    try:
        result = subprocess.run(
            [str(bash_path), "-lc", "echo ok"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "ok" in result.stdout


def _is_wsl_bash_stub(path: Path) -> bool:
    """Heuristica: bash.exe bajo System32/SysWOW64 suele ser el relay de WSL."""
    parts = {part.lower() for part in path.parts}
    return path.name.lower() == "bash.exe" and bool(parts & set(_WSL_BASH_STUB_DIRS))


def _git_bash_candidates() -> list[Path]:
    """Rutas conocidas de Git for Windows (prioridad sobre PATH)."""
    return [p for p in _GIT_BASH_CANDIDATES if p.is_file()]


def _path_bash_candidates() -> list[Path]:
    """Candidatos desde PATH, excluyendo stubs WSL conocidos."""
    found = shutil.which("bash")
    if not found:
        return []
    path = Path(found)
    if _is_wsl_bash_stub(path):
        return []
    return [path]


def resolve_bash() -> str | None:
    """Devuelve la ruta a un ejecutable bash funcional o `None`."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        # Git Bash antes que `which bash` (evita el stub WSL de System32).
        candidates.extend(_git_bash_candidates())
        candidates.extend(_path_bash_candidates())
    else:
        if found := shutil.which("bash"):
            candidates.append(Path(found))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _bash_is_usable(candidate):
            return str(candidate)
    return None


def resolve_shell_choice(shell: ShellChoice) -> ResolvedShell:
    """Resuelve `auto`/`bash`/`cmd` al intérprete efectivo."""
    if shell == "cmd":
        return "cmd"
    if shell == "bash":
        return "bash"
    if sys.platform == "win32":
        return "bash" if resolve_bash() else "cmd"
    return "bash"


def _normalize_cwd(cwd: str) -> str:
    """Normaliza rutas para Git Bash en Windows."""
    if sys.platform == "win32":
        return cwd.replace("\\", "/")
    return cwd


class BashLocalEnvironment(LocalEnvironment):
    """`LocalEnvironment` que ejecuta comandos con `bash -lc`."""

    def __init__(
        self,
        *,
        bash_executable: str | None = None,
        config_class: type = LocalEnvironmentConfig,
        **kwargs: Any,
    ) -> None:
        """Inicializa `BashLocalEnvironment`."""
        resolved = bash_executable or resolve_bash()
        if not resolved:
            raise RuntimeError(
                "No se encontro bash. Instala Git for Windows o anade bash al PATH, "
                "o usa --shell cmd."
            )
        self._bash_executable = resolved
        super().__init__(config_class=config_class, **kwargs)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Ejecuta un comando en bash y devuelve el resultado como dict."""
        command = action.get("command", "")
        effective_cwd = _normalize_cwd(cwd or self.config.cwd or os.getcwd())
        try:
            result = subprocess.run(
                [self._bash_executable, "-lc", command],
                text=True,
                cwd=effective_cwd,
                env=os.environ | self.config.env,
                timeout=timeout or self.config.timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        """Variables de plantilla con `shell=bash` y la ruta del ejecutable."""
        template_vars = super().get_template_vars(**kwargs)
        template_vars["shell"] = "bash"
        template_vars["bash_executable"] = self._bash_executable
        return template_vars


def build_local_environment(
    *,
    cwd: str,
    shell: ShellChoice = "auto",
    **kwargs: Any,
) -> LocalEnvironment:
    """Construye `BashLocalEnvironment` o `LocalEnvironment` segun `shell`."""
    resolved = resolve_shell_choice(shell)
    if resolved == "bash":
        return BashLocalEnvironment(cwd=cwd, **kwargs)
    if sys.platform != "win32":
        raise RuntimeError("El intérprete cmd solo esta soportado en Windows.")
    return LocalEnvironment(cwd=cwd, **kwargs)


def describe_shell(shell: ShellChoice) -> str:
    """Texto legible del intérprete efectivo (para logs del CLI)."""
    resolved = resolve_shell_choice(shell)
    if resolved == "bash":
        bash = resolve_bash()
        return f"bash ({bash or 'desconocido'})"
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    if shell == "auto" and sys.platform == "win32" and resolve_bash() is None:
        return f"cmd ({comspec}; bash no encontrado)"
    return f"cmd ({comspec})"
