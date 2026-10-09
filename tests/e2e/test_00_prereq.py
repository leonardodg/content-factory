import os
import shutil
import socket

import pytest

pytestmark = pytest.mark.e2e


def test_ram_minima() -> None:
    with open("/proc/meminfo") as f:
        mem_available = next(line for line in f if line.startswith("MemAvailable"))
    available_kb = int(mem_available.split()[1])
    assert available_kb > 1_500_000, f"RAM disponível {available_kb} kB < 1.5 GB"


def test_postgres_up() -> None:
    assert os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL ausente"
    host = os.getenv("TEST_DB_HOST", "127.0.0.1")
    with socket.create_connection((host, 5432), timeout=3):
        pass


def test_docker_presente() -> None:
    assert shutil.which("docker"), "docker não encontrado"
