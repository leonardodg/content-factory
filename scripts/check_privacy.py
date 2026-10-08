"""Falha se segredo/key/path pessoal aparecer em arquivo rastreado."""

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "chave privada"),
    (
        r"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}",
        "credencial literal",
    ),
    (r"/home/[a-z]+/", "path pessoal"),
]
ALLOWLIST = {
    ".env.example",
    "check_privacy.py",
    "secrets.env.example",
    "app.env.example",
    "2026-10-07-content-factory-design.md",
    # planos citam o próprio guard e o compose (mesmo tratamento do design spec)
    "2026-10-07-fundacao-coletores-banco.md",
    # paths /home/<user> de container, não de pessoa
    "docker-compose.yml",
}


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bad = []
    for name in files:
        if Path(name).name in ALLOWLIST or name.endswith((".png", ".svg", ".lock")):
            continue
        try:
            text = Path(name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in PATTERNS:
            if re.search(pattern, text):
                bad.append(f"{name}: {label}")
    if bad:
        print("PRIVACIDADE FALHOU:\n" + "\n".join(bad))
        return 1
    print(f"privacidade ok ({len(files)} arquivos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
