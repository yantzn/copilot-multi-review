from __future__ import annotations

import subprocess
from pathlib import Path


def decode_output(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            return data.decode("cp932", errors="strict")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def run_command(args: list[str], *, cwd: Path, timeout_seconds: int = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        shell=False,
    )
