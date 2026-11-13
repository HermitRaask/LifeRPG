import subprocess
import sys
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "main.py"


def run_cli(*args, commands):
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(MAIN), *args],
        input=commands,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    ).stdout


def test_menu_and_restart(tmp_path):
    path = str(tmp_path / "state.json")
    output = run_cli(
        "--state", path,
        commands=(
            "x\n1\nтренир\nТело\n2\nunknown\n2\ntraining\n2\ntraining\n"
            "3\n4\n5\n1\nreading\n6\n2\n7\n8\n0\n"
        ),
    )
    assert "Неизвестная команда." in output
    assert "training — Тренировка (Тело)" in output
    assert "Активность с таким кодом не найдена." in output
    assert "Сила: уровень 2, 0/125 XP" in output
    assert "#1 — Тренировка (+60 XP)" in output
    assert "Исправление: #1 — Чтение (+4 XP)" in output
    assert "Исправление: #2 — Отменено" in output
    assert "Выполнений: 1, всего XP: 4" in output

    restored = run_cli("--state", path, commands="3\n4\n0\n")
    assert "Сила: уровень 1, 0/100 XP" in restored
    assert "Интеллект: уровень 1, 2/100 XP" in restored
    assert "#1 — Чтение (+4 XP)" in restored
    assert "Интеллект +2 XP, Творчество +2 XP" in restored


def test_pr01_demo():
    output = run_cli("--demo-pr01", commands="350\n")
    assert "Сила: уровень 3, 125/150 XP" in output


def test_audit_shows_xp_by_attribute_after_restart(tmp_path):
    path = str(tmp_path / "state.json")
    run_cli("--state", path, commands="2\neagle\n5\n1\nreading\n6\n1\n0\n")
    output = run_cli("--state", path, commands="8\n0\n")

    assert "Выполнение: #1 — Стойка орла (+65 XP)" in output
    assert "Воля +50 XP, Выносливость +15 XP" in output
    assert "Интеллект +2 XP, Творчество +2 XP" in output
    assert "Без начисления XP" in output


def test_journal_cli_restores_levels_and_repeat_count(tmp_path):
    path = str(tmp_path / "events.jsonl")
    output = run_cli(
        "--state", path, commands="2\nmeditation\n\n2\nmeditation\n\n3\n0\n"
    )
    assert "Медитация: Воля +50 XP" in output
    assert "Воля: уровень 2, 0/125 XP" in output
    assert "Повтор за игровой день: 2" in output

    restored = run_cli(
        "--state", path, commands="3\n1\nМедитация\n\n4\n0\n"
    )
    assert "Воля: уровень 2, 0/125 XP" in restored
    assert "выполнено 2; следующее ×0.6" in restored
    assert "#3 — Медитация" in restored


def test_journal_cli_revises_and_revokes_level_after_restart(tmp_path):
    path = str(tmp_path / "events.jsonl")
    output = run_cli(
        "--state", path,
        commands="2\nmeditation\n\n2\nmeditation\n\n6\n3\n3\n0\n",
    )
    assert "Воля: уровень 2 снят" in output
    assert "Воля: уровень 1, 50/100 XP" in output

    output = run_cli(
        "--state", path, commands="5\n2\nreading\n\n3\n4\n8\n0\n"
    )
    assert "#2 — Чтение" in output
    assert "#3 — Отменено" in output
    assert "Воля: уровень 1, 0/100 XP" in output
    assert "Интеллект: уровень 1, 2/100 XP" in output
    assert "Изменены пороги уровней" in output
    assert "Воля: уровень 2 снят" in output
