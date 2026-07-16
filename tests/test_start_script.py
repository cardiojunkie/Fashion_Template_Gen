from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "start.sh"


def run_script(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd or ROOT,
        env={**os.environ, "PYTHON_BIN": "/bin/echo", **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )


def test_start_script_syntax_permissions_and_foreground_exec() -> None:
    source = SCRIPT.read_text()

    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    assert source.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert 'exec "$PYTHON_BIN" -m streamlit run app.py' in source
    assert not any(line.rstrip().endswith("&") for line in source.splitlines())


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_invalid_port_fails(port: str) -> None:
    result = run_script(port)

    assert result.returncode == 2
    assert result.stderr == "Error: port must be an integer between 1 and 65535.\n"


def test_positional_port_overrides_port_environment() -> None:
    result = run_script("8502", env={"PORT": "8503"})

    assert result.returncode == 0
    assert "Local URL: http://localhost:8502" in result.stdout
    assert "--server.address 0.0.0.0 --server.port 8502" in result.stdout


def test_port_environment_is_used_without_positional_port() -> None:
    result = run_script(env={"PORT": "8503", "HOST": "127.0.0.1"})

    assert result.returncode == 0
    assert "Local URL: http://localhost:8503" in result.stdout
    assert "--server.address 0.0.0.0 --server.port 8503" in result.stdout
    assert "--server.headless true --browser.gatherUsageStats false" in result.stdout


def test_launcher_changes_to_repository_root_and_prints_codespaces_url(tmp_path: Path) -> None:
    capture = tmp_path / "capture.txt"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then exit 0; fi\n'
        'printf "%s\\n" "$PWD" "$@" > "$CAPTURE"\n'
    )
    fake_python.chmod(0o755)

    result = run_script(
        "8510",
        cwd=tmp_path,
        env={
            "PYTHON_BIN": str(fake_python),
            "CAPTURE": str(capture),
            "CODESPACES": "true",
            "CODESPACE_NAME": "fashion-space",
            "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
        },
    )

    assert result.returncode == 0
    assert capture.read_text().splitlines() == [
        str(ROOT),
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.address",
        "0.0.0.0",
        "--server.port",
        "8510",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    assert (
        "Codespaces URL: https://fashion-space-8510.app.github.dev" in result.stdout
    )


def test_missing_streamlit_has_useful_error() -> None:
    result = run_script(env={"PYTHON_BIN": "/bin/false"})

    assert result.returncode == 1
    assert "Streamlit is not installed for /bin/false" in result.stderr
    assert "Install the project dependencies" in result.stderr
