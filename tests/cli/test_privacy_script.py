import subprocess
import sys


def test_privacy_script_passes_on_repo() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_privacy.py"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
