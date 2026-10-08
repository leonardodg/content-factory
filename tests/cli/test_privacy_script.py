import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_privacy.py"


def test_privacy_script_passes_on_repo() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_privacy.py"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _init_repo(tmp_path: Path, content: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "fixture.txt").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "fixture.txt"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _run_privacy(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True)


def test_flags_quoted_dict_and_bare_token_credentials(tmp_path: Path) -> None:
    # segredo sintético vive só no fixture tmp; no fonte, keyword e valor
    # vêm de literais separados para o guard do repo continuar verde
    quoted = "a = {'access_token': '" + "sk-live-0123456789abcdef" + "'}\n"
    bare = 'b = "token: ' + "ghp_0123456789abcdef0123" + '"\n'
    proc = _run_privacy(_init_repo(tmp_path, quoted + bare))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "credencial literal" in proc.stdout


def test_ignores_placeholder_credentials(tmp_path: Path) -> None:
    quoted = "a = {'access_token': '" + "YOUR_ACCESS_TOKEN" + "'}\n"
    bare = 'b = "token: ' + "YOUR_SECRET_TOKEN_HERE" + '"\n'
    proc = _run_privacy(_init_repo(tmp_path, quoted + bare))
    assert proc.returncode == 0, proc.stdout + proc.stderr
