"""Exercise the installed EXE with an isolated profile and real Edge UI."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from tempfile import mkdtemp

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parents[1]
exe = Path(
    sys.argv[1] if len(sys.argv) > 1 else BASE / "LifeRPG.exe"
).resolve()
workspace = Path(mkdtemp(prefix="desktop-catalog-", dir=BASE / ".venv"))
journal = workspace / "data/web/events.jsonl"
process = None


def events():
    return [json.loads(line) for line in journal.read_text(
        encoding="utf-8").splitlines()]


def launch():
    log = workspace / "desktop.log"
    offset = log.stat().st_size if log.exists() else 0
    worker = subprocess.Popen(
        [str(exe), "--workspace", str(workspace)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(120):
        log = workspace / "desktop.log"
        if log.exists():
            urls = re.findall(r"http://127[.]0[.]0[.]1:\d+/",
                              log.read_bytes()[offset:].decode("utf-8"))
            if urls:
                return worker, urls[-1]
        if worker.poll() is not None:
            raise AssertionError("EXE exited; inspect " + str(workspace))
        time.sleep(.25)
    worker.terminate()
    raise AssertionError("Packaged server did not start")


def completion(page, url, activity):
    page.goto(url + "activities/?activity=" + activity)
    page.get_by_role("button", name="Рассчитать награду").click()
    page.get_by_role("button", name="Подтвердить выполнение").click()
    page.wait_for_url("**/result/**")


try:
    process, url = launch()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.goto(url)
        page.get_by_role("button", name="Пропустить", exact=True).click()
        page.get_by_role("button", name="Пустой профиль", exact=True).click()
        assert page.locator(".page-footer").inner_text().endswith(
            "Что наша жизнь? Игра!"
        )
        assert page.locator(".brand small").count() == 0
        page.goto(url + "settings/")
        assert page.locator(".settings-grid > .panel").count() == 6
        headings = page.locator(".settings-grid > .panel > h2")
        assert headings.all_text_contents() == [
            "Управление элементами", "Системные настройки",
            "Отображение элементов", "Резервная копия",
            "Игровое время и сохранение", "Обучение",
        ]

        def check_centering():
            for panel in page.locator(".settings-action-panel").all():
                outer = panel.bounding_box()
                group = panel.locator(
                    ".display-settings-actions"
                ).bounding_box()
                assert abs(group["y"] + group["height"] / 2
                           - outer["y"] - outer["height"] / 2) < 2
                buttons = panel.locator(".display-settings-actions > .btn")
                previous = None
                for button in buttons.all():
                    box = button.bounding_box()
                    assert abs(box["x"] + box["width"] / 2
                               - outer["x"] - outer["width"] / 2) < 2
                    if previous:
                        assert box["y"] > previous["y"] + previous["height"]
                        assert abs(box["width"] - previous["width"]) < 1
                    previous = box
        check_centering()
        page.screenshot(path=workspace / "settings-desktop.png",
                        full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        check_centering()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth"
        )
        page.screenshot(path=workspace / "settings-mobile.png", full_page=True)
        page.set_viewport_size({"width": 1280, "height": 720})
        page.goto(url + "achievements/")
        assert page.locator(".achievement-card").count() == 5
        page.goto(url + "activities/")
        assert page.locator(".activity-tile").count() == 0
        page.goto(url + "create-activity/")
        page.get_by_label("Название").fill("Учебная активность")
        page.get_by_label("Категория").fill("Пример")
        page.get_by_label("Воля · базовый XP").fill("100")
        page.get_by_role("button", name="Создать активность").click()
        activity = events()[-1]["entity_id"]
        page.goto(url + "create-skill/")
        page.get_by_label("Название").fill("Учебный навык")
        page.locator("#skill-link-activity").select_option(activity)
        page.locator("#skill-link-xp").fill("1000")
        page.get_by_role("button", name="Применить", exact=True).click()
        page.get_by_role("button", name="Создать навык", exact=True).click()
        skill = events()[-1]["entity_id"]
        page.goto(url + "create-quest/")
        page.get_by_label("Название").fill("Учебное задание")
        page.get_by_label("Активность").select_option(activity)
        page.get_by_label("Число выполнений").fill("1")
        page.get_by_label("Описание условия выполнения").fill("Один раз")
        page.get_by_role("button", name="Создать задание").click()
        quest = events()[-1]["entity_id"]
        completion(page, url, activity)
        progress = events()[-1]["payload"]["root_skill_progress"][skill]
        assert progress["level"] == 2
        page.goto(url + "developer-edit/?kind=quest&target=" + quest)
        page.get_by_label("Описание условия выполнения").fill("Новое условие")
        page.get_by_role("button", name="Сохранить тексты").click()
        assert page.locator('[name="target"]').input_value() == quest
        condition = page.get_by_label("Описание условия выполнения")
        assert condition.input_value() == "Новое условие"
        page.goto(url + "catalog/?kind=activity&key=" + activity)
        page.get_by_role(
            "button", name="Архивировать вместе с зависимыми заданиями"
        ).click()
        page.goto(url + "activities/")
        assert page.locator(".activity-tile").count() == 0
        page.goto(url + "elements/")
        page.get_by_label("Показывать режим разработчика").check()
        page.get_by_role("button", name="Сохранить отображение").click()
        page.goto(url + "developer/")
        page.get_by_role("button", name="Включить режим", exact=True).click()
        page.get_by_role("link", name="Архив", exact=True).click()
        page.locator('input[value="activity:' + activity + '"]').check()
        page.locator('input[value="quest:' + quest + '"]').check()
        page.get_by_role("button", name="Разархивировать выбранное").click()
        completion(page, url, activity)
        last = events()[-1]["payload"]
        assert last["root_skill_progress"][skill]["level"] == 2
        assert last["quest_state"]["streaks"][quest]["duration"] == 1
        page.goto(url + "settings/")
        page.get_by_role("button", name="Пройти обучение", exact=True).click()
        for step in range(23):
            guide = page.locator(".tutorial-guide")
            assert guide.is_visible()
            assert page.locator("#main button:enabled").count() == 0
            assert page.locator("#main input:enabled").count() == 0
            if step == 1:
                guide.get_by_role("button", name="Назад", exact=True).click()
                assert page.locator("#main button:enabled").count() == 0
                page.locator(".tutorial-guide").get_by_role(
                    "button", name="Далее", exact=True
                ).click()
            assert guide.locator("h2").evaluate(
                "e => getComputedStyle(e).outlineStyle"
            ) == "none"
            if step == 0:
                assert page.locator(".quick-panel").count() == 0
                assert page.locator(".secondary-column").count() == 0
            if step == 8:
                assert page.get_by_role(
                    "heading", name="Учебный навык", exact=True
                ).is_visible()
            expected = {
                9: ".skill-mastery", 15: "#display-settings",
                16: "#system-settings", 17: "#game-time-settings",
                18: "#backup-settings", 22: "#developer-tools",
            }
            if step in expected:
                assert page.locator(expected[step]).first.evaluate(
                    "e => e.classList.contains('tutorial-highlight')"
                )
            if step == 10:
                original_size = page.viewport_size
                for size in ({"width": 1280, "height": 720},
                             {"width": 390, "height": 844}):
                    page.set_viewport_size(size)
                    page.wait_for_timeout(200)
                    row = page.locator(".bank-row").bounding_box()
                    bottoms = [
                        panel.bounding_box()["y"]
                        + panel.bounding_box()["height"]
                        for panel in page.locator(".bank-row > .panel").all()
                    ]
                    row_bottom = row["y"] + row["height"]
                    assert abs(row_bottom - max(bottoms)) < 1
                    heading = page.get_by_role(
                        "heading", name="Ежедневные", exact=True
                    ).bounding_box()
                    assert heading["y"] - row_bottom >= 16
                page.screenshot(path=workspace / "tutorial-freezes.png",
                                full_page=True)
                page.set_viewport_size(original_size)
            if step == 9:
                page.screenshot(path=workspace / "tutorial-mastery.png",
                                full_page=True)
            label = "Завершить" if step == 22 else "Далее"
            guide.get_by_role("button", name=label, exact=True).click()
        before = events()
        page.screenshot(path=workspace / "installed.png", full_page=True)
        process.terminate()
        process.wait(15)
        process, url = launch()
        page.goto(url)
        assert page.locator(".stat-card").count() == 6
        assert events() == before
        browser.close()
    print("Installed EXE: empty profile, normal editor, skill XP, cascade, "
          "restore, tutorial and restart passed. Artifacts:", workspace)
finally:
    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(15)
