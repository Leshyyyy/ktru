"""Lock-файлы зависимостей не отстают от исходных списков и не расходятся между собой.

Lock собирается командой из backend/CLAUDE.md; эти тесты ловят, что её забыли запустить.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.core.settings import REPO_ROOT

BACKEND = REPO_ROOT / "backend"
# Нужны ML и backend/files только для тестов — в образ бэкенда не идут.
TEST_ONLY = {"pytest"}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requested(path: Path) -> set[str]:
    """Имена пакетов из requirements-файла без версий и extras; -r/-c и комментарии пропускаются."""
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.add(_normalize(re.split(r"[\s\[<>=!~;]", line, maxsplit=1)[0]))
    return names


def _locked(path: Path) -> dict[str, str]:
    return {
        _normalize(n): v
        for n, v in re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", path.read_text(encoding="utf-8"), re.M)
    }


def test_lock_contains_every_requested_package() -> None:
    for source, lock in (
        ("requirements.in", "requirements.txt"),
        ("requirements-dev.in", "requirements-dev.txt"),
        ("requirements-ml.in", "requirements-ml.txt"),
    ):
        missing = sorted(_requested(BACKEND / source) - set(_locked(BACKEND / lock)))
        assert not missing, f"{lock} отстал от {source}, пересоберите lock: {missing}"


def test_ml_runtime_dependencies_are_in_backend_image() -> None:
    """worker-ml в режиме real запускает ml.pipeline из образа бэкенда — зависимости ML должны быть в его lock."""
    missing = sorted(
        _requested(REPO_ROOT / "ml" / "requirements.txt") - TEST_ONLY - set(_locked(BACKEND / "requirements.txt"))
    )
    assert not missing, f"Добавьте в backend/requirements.in и пересоберите lock: {missing}"


def test_files_dependencies_are_in_backend_image() -> None:
    """api и worker-io вызывают backend.files — его зависимости должны быть в lock образа."""
    missing = sorted(
        _requested(BACKEND / "files" / "requirements.txt") - TEST_ONLY - set(_locked(BACKEND / "requirements.txt"))
    )
    assert not missing, f"Добавьте в backend/requirements.in и пересоберите lock: {missing}"


@pytest.mark.parametrize("lock", ["requirements-dev.txt", "requirements-ml.txt"])
def test_locks_pin_the_same_runtime_versions(lock: str) -> None:
    """CI тестирует ровно те версии, что попадут в образы; worker-ml работает на тех же версиях, что api."""
    runtime, other = _locked(BACKEND / "requirements.txt"), _locked(BACKEND / lock)
    differ = {name: (version, other.get(name)) for name, version in runtime.items() if other.get(name) != version}
    assert not differ, f"Версии в {lock} расходятся с requirements.txt: {differ}"


def test_ml_lock_has_cpu_torch_only() -> None:
    """Обычный torch с PyPI под Linux тянет библиотеки CUDA на 2+ ГБ — в образе worker-ml только CPU-сборка."""
    text = (BACKEND / "requirements-ml.txt").read_text(encoding="utf-8")
    assert re.search(r"^torch==[^\s;]+\+cpu ", text, re.M), "torch должен быть CPU-сборкой (--torch-backend cpu)"
    gpu = sorted({n for n in _locked(BACKEND / "requirements-ml.txt") if n.startswith(("nvidia-", "triton"))})
    assert not gpu, f"В lock worker-ml попали пакеты CUDA: {gpu}"
