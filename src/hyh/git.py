from typing import Final

from .runtime import ExecutionResult, LocalRuntime

_runtime: Final = LocalRuntime()

_DANGEROUS_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "-c",
        "--config",
        "--upload-pack",
        "--exec",
        "-u",
        "--receive-pack",
    }
)

_DANGEROUS_PREFIXES: Final[tuple[str, ...]] = (
    "-c=",
    "--config=",
    "--upload-pack=",
    "--exec=",
    "--receive-pack=",
)


def _validate_git_args(args: list[str]) -> None:
    for arg in args:
        if arg in _DANGEROUS_OPTIONS:
            raise ValueError(
                f"Dangerous git option '{arg}' is not allowed. "
                "This option could enable command injection."
            )

        for prefix in _DANGEROUS_PREFIXES:
            if arg.startswith(prefix):
                raise ValueError(
                    f"Dangerous git option '{prefix.rstrip('=')}' is not allowed. "
                    "This option could enable command injection."
                )


def safe_git_exec(
    args: list[str],
    cwd: str,
    timeout: int = 60,
    read_only: bool = False,
) -> ExecutionResult:
    _validate_git_args(args)

    return _runtime.execute(
        ["git", *args],
        cwd=cwd,
        timeout=timeout,
        exclusive=not read_only,
    )


def safe_commit(cwd: str, message: str) -> ExecutionResult:
    from .runtime import GLOBAL_EXEC_LOCK

    with GLOBAL_EXEC_LOCK:
        add_result = _runtime.execute(
            ["git", "add", "-A"],
            cwd=cwd,
            exclusive=False,
        )
        if add_result.returncode != 0:
            return add_result

        return _runtime.execute(
            ["git", "commit", "-m", message],
            cwd=cwd,
            exclusive=False,
        )


def get_head_sha(cwd: str) -> str | None:
    result = safe_git_exec(["rev-parse", "HEAD"], cwd=cwd, read_only=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None
