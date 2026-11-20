"""Real Edge smoke test with a disposable save and server restart."""
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

BASE = Path(__file__).resolve().parents[1]
ARTIFACTS = BASE / ".venv" / ("browser-" + uuid4().hex[:10])
ARTIFACTS.mkdir(parents=True)
os.environ["LIFERPG_WEB_DATA"] = str(ARTIFACTS)
os.environ["LIFERPG_WEB_DB"] = str(ARTIFACTS / "db.sqlite3")
os.environ["DJANGO_SETTINGS_MODULE"] = "webconfig.settings"
(ARTIFACTS / "tmp").mkdir()
os.environ["TMP"] = os.environ["TEMP"] = str(ARTIFACTS / "tmp")
sys.path.insert(0, str(BASE))

import django  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

django.setup()
from django.db import connections  # noqa: E402
from webgame import services  # noqa: E402

URL = "http://127.0.0.1:8002"
subprocess.run(
    [sys.executable, "manage.py", "migrate", "--noinput"],
    cwd=BASE, check=True, stdout=subprocess.DEVNULL,
)
server_log = (ARTIFACTS / "server.log").open("w", encoding="utf-8")


def start_server():
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", 8002)) == 0:
            raise RuntimeError("Port 8002 is already in use")
    process = subprocess.Popen(
        [sys.executable, "manage.py", "runserver",
         "127.0.0.1:8002", "--noreload"],
        cwd=BASE, stdout=server_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("Server failed; inspect " + str(ARTIFACTS))
        try:
            with urlopen(URL, timeout=1) as response:
                if response.status == 200:
                    return process
        except (OSError, URLError):
            time.sleep(0.2)
    process.terminate()
    raise RuntimeError("Server did not start")


def stop_server(process):
    process.terminate()
    process.wait(timeout=10)
    # Windows can release the listening socket shortly after process exit.
    for _ in range(50):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", 8002)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError("Owned test server has not released port 8002")


def read_state(**kwargs):
    # Playwright owns an event loop; ORM queries run in their own sync thread.
    def read():
        try:
            return services.refresh(**kwargs)
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result()


def activity(page, key):
    page.goto(URL + "/activities/?activity=" + key)
    page.get_by_label("Активность").select_option(key)
    page.get_by_role("button", name="Рассчитать награду").click()
    page.get_by_role("button", name="Подтвердить выполнение").click()
    page.wait_for_url("**/result/**")
    assert page.get_by_role("heading", name="Действие сохранено").is_visible()


def no_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    ), "Horizontal overflow at " + page.url


process = start_server()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(URL)
        page.get_by_role("button", name="Пройти обучение", exact=True).click()
        for step in range(23):
            guide = page.locator(".tutorial-guide")
            assert guide.is_visible()
            assert page.locator("#main button:enabled").count() == 0
            assert page.locator("#main input:enabled").count() == 0
            assert page.locator(".tutorial-highlight").count() == 1
            assert guide.locator("h2").evaluate(
                "e => getComputedStyle(e).outlineStyle"
            ) == "none"
            expected_targets = {
                9: ".skill-card .skill-mastery",
                15: "#display-settings", 16: "#system-settings",
                17: "#game-time-settings", 18: "#backup-settings",
                22: "#developer-tools",
            }
            if step in expected_targets:
                assert page.locator(expected_targets[step]).first.evaluate(
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
                page.screenshot(path=ARTIFACTS / "tutorial-freezes.png",
                                full_page=True)
                page.set_viewport_size(original_size)
            if step == 22:
                tools = page.locator("#developer-tools").bounding_box()
                banner = page.locator(".developer-banner").bounding_box()
                assert tools["y"] >= banner["y"] + banner["height"]
                for tile in page.locator("#developer-tools .panel").all():
                    box = tile.bounding_box()
                    assert box["y"] >= tools["y"]
                    assert box["y"] + box["height"] <= (
                        tools["y"] + tools["height"] + 1
                    )
            if step == 1:
                guide.get_by_role("button", name="Назад", exact=True).click()
                assert page.locator("#main button:enabled").count() == 0
                page.locator(".tutorial-guide").get_by_role(
                    "button", name="Далее", exact=True
                ).click()
            if step == 2:
                no_js = browser.new_context(java_script_enabled=False)
                no_js.add_cookies(context.cookies())
                static_page = no_js.new_page()
                static_page.goto(URL + "/tutorial/")
                assert static_page.locator("#main button").count() > 0
                assert static_page.locator("#main button:enabled").count() == 0
                no_js.close()
            if step == 4:
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(300)
                no_overflow(page)
                highlight = page.locator(".tutorial-highlight").bounding_box()
                assert highlight["y"] < 150
                page.screenshot(path=ARTIFACTS / "tutorial-mobile.png",
                                full_page=True)
            page.evaluate("""
                const form = document.querySelector(".tutorial-guide form");
                form.addEventListener("submit", () => {
                  const button = form.querySelector(
                    '[value="next"], [value="finish"]');
                  sessionStorage.setItem("tutorialSubmitStayedEnabled",
                    String(!button.disabled));
                });
            """)
            label = "Завершить" if step == 22 else "Далее"
            guide.get_by_role("button", name=label, exact=True).click()
            assert page.evaluate(
                "sessionStorage.getItem('tutorialSubmitStayedEnabled')"
            ) == "true"
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.get_by_role("button", name="Предустановленный профиль").click()
        assert page.locator(".page-footer").inner_text().endswith(
            "Что наша жизнь? Игра!"
        )
        assert page.locator(".brand small").count() == 0
        assert page.locator(".stat-card").count() == 6
        assert page.get_by_role(
            "heading", name="Задания на неделю"
        ).is_visible()
        assert not page.get_by_role("heading", name="Перки").is_visible()
        page.goto(URL + "/settings/")
        settings_cards = page.locator(".settings-grid .panel > h2")
        assert settings_cards.all_text_contents() == [
            "Управление элементами", "Системные настройки",
            "Отображение элементов", "Резервная копия",
            "Игровое время и сохранение", "Обучение",
        ]
        for panel in page.locator(".settings-action-panel").all():
            outer = panel.bounding_box()
            group = panel.locator(".display-settings-actions").bounding_box()
            assert abs(group["y"] + group["height"] / 2
                       - outer["y"] - outer["height"] / 2) < 2
            buttons = panel.locator(".display-settings-actions > .btn")
            for button in buttons.all():
                box = button.bounding_box()
                assert abs(box["x"] + box["width"] / 2
                           - outer["x"] - outer["width"] / 2) < 2
        page.screenshot(path=ARTIFACTS / "settings-centered.png",
                        full_page=True)
        page.get_by_role("link", name="Отображение заданий").click()
        page.wait_for_url("**/display/")
        daily_mode = page.locator('[name="daily_mode"]')
        weekly_mode = page.locator('[name="weekly_mode"]')
        daily_mode.select_option("manual")
        weekly_mode.select_option("manual")
        daily = page.locator('[name="daily_pins"][value="writing"]')
        weekly = page.locator('[name="weekly_pins"][value="eagle"]')
        daily.check()
        weekly.check()
        page.get_by_role("button", name="Сохранить отображение").click()
        page.goto(URL + "/elements/")
        page.get_by_label("Показывать режим разработчика").check()
        page.get_by_role("button", name="Сохранить отображение").click()
        page.goto(URL + "/display/")
        assert daily.is_checked() and weekly.is_checked()
        page.get_by_role("button", name="Показывать все задания").click()
        assert not daily.is_visible() and not weekly.is_visible()
        daily_mode.select_option("manual")
        weekly_mode.select_option("manual")
        assert daily.is_checked() and weekly.is_checked()
        page.get_by_role("button", name="Сохранить отображение").click()
        page.goto(URL)
        panels = page.locator(".quest-columns > .panel")
        assert panels.count() == 2
        boxes = [panels.nth(i).bounding_box() for i in range(2)]
        assert abs(boxes[0]["y"] - boxes[1]["y"]) < 2
        assert abs(boxes[0]["width"] - boxes[1]["width"]) < 2
        page.goto(URL + "/stats/")
        assert page.get_by_role("heading", name="Модификаторы").is_visible()
        assert page.get_by_role("heading", name="Базовые навыки").is_visible()
        page.screenshot(path=ARTIFACTS / "desktop.png", full_page=True)
        no_overflow(page)
        activity(page, "meditation")
        assert page.locator(".event-toast").count() >= 1
        page.locator(".event-toast").first.click()
        page.get_by_label("Последние уведомления").click()
        assert page.get_by_role("link", name="Все уведомления").is_visible()
        page.keyboard.press("Escape")
        activity(page, "meditation")
        assert page.locator(".event-toast").count() >= 2
        page.wait_for_timeout(5400)
        assert page.locator(".event-toast").count() == 0
        page.goto(URL + "/notifications/")
        assert page.locator(".audit-row").count() >= 3

        state = read_state()[1]
        assert state["stats"]["will"]["level"] == 2
        assert state["stats"]["will"]["xp"] == 24
        assert "first-level" in state["achievements"]
        activity(page, "writing")
        assert read_state()[1]["writing"]["xp"] == 50
        page.reload()
        assert read_state()[1]["writing"]["xp"] == 50
        page.goto(URL + "/history/")
        page.get_by_role("link", name="Исправить или отменить").first.click()
        page.get_by_label("Интенсивность").select_option("3")
        page.get_by_role("button", name="Сохранить исправление").click()
        page.wait_for_url("**/result/**")
        page.goto(URL + "/developer/")
        page.get_by_role("button", name="Включить режим").click()
        page.goto(URL + "/settings/")
        page.get_by_role("link", name="Изменить элемент").click()
        page.wait_for_url("**/developer-edit/")
        editor = page.locator("#content-edit")
        editor.locator('[name="kind"]').select_option("quest")
        editor.locator('[name="target"]').select_option("writing")
        assert editor.locator("#edit-name").is_disabled()
        assert editor.locator("#edit-details").is_disabled()
        description = editor.locator('[name="condition_text"]')
        original = description.input_value()
        description.fill("Несохранённый текст")
        editor.locator('[name="target"]').select_option("meditation")
        editor.locator('[name="target"]').select_option("writing")
        assert description.input_value() == original
        description.fill("Проверенное описание")
        editor.get_by_role("button", name="Сохранить тексты").click()
        page.goto(URL + "/quests/")
        assert page.get_by_text(
            "Проверенное описание", exact=True
        ).is_visible()
        page.goto(URL + "/developer/")
        page.goto(URL + "/settings/")
        page.get_by_role("link", name="Добавить элемент").click()
        page.get_by_role("link", name="Создать навык").click()
        skill = page.locator('form[action="/action/create-skill/"]')
        for chosen, xp in (("walk", "50"), ("run", "100"), ("walk", "1100")):
            skill.locator("#skill-link-activity").select_option(chosen)
            skill.locator("#skill-link-xp").fill(xp)
            skill.get_by_role("button", name="Применить").click()
        assert page.locator("#skill-link-list li").count() == 2
        assert "1100 XP" in page.locator("#skill-link-list").inner_text()
        skill.get_by_role("button", name="Создать навык").click()
        assert page.locator("#skill-link-list li").count() == 2
        skill.get_by_label("Название").fill("Базовый навык прогулки")
        skill.get_by_role("button", name="Создать навык").click()
        activity(page, "walk")
        page.goto(URL + "/skills/")
        assert page.get_by_role(
            "heading", name="Базовый навык прогулки"
        ).is_visible()
        # New achievement targets are selected in the real form.
        skill_key = next(
            event["entity_id"] for event in read_state()[0]
            if event["event_type"] == "skill.created"
        )
        for mode, target, title in (
            ("stat", "any", "Любая характеристика"),
            ("skill", skill_key, "Выбранный базовый навык"),
            ("skill", "any", "Любой базовый навык"),
        ):
            page.goto(URL + "/create-achievement/")
            creator = page.locator(
                'form[action="/action/create-achievement/"]'
            )
            creator.get_by_label("Название").fill(title)
            creator.locator('[name="condition_type"]').select_option(mode)
            assert creator.locator(f'[name="{mode}"]').is_visible()
            creator.locator(f'[name="{mode}"]').select_option(target)
            creator.get_by_label("Порог").fill("2")
            creator.get_by_label("Описание условия получения").fill(title)
            creator.get_by_role("button", name="Создать достижение").click()
            created = read_state()[0][-1]
            assert created["payload"]["consequences"] == [{
                "type": "achievement.unlocked",
                "achievement": created["entity_id"],
            }]
            page.goto(URL + "/achievements/")
            assert page.locator(".achievement-card.unlocked").filter(
                has=page.get_by_role("heading", name=title, exact=True)
            ).count() == 1
        page.goto(URL + "/stats/#perks")
        page.get_by_role("button", name="Выключить Ленивый").first.click()
        assert "lazy" not in read_state()[1]["perks"]
        page.goto(URL + "/developer/")
        page.goto(URL + "/create-activity/")
        creator = page.locator('form[action="/action/create-activity/"]')
        creator.get_by_label("Название").fill("Прогулка в парке")
        creator.get_by_label("Категория").fill("На улице")
        creator.get_by_label("Воля · базовый XP").fill("10")
        assert not creator.locator('[name="stat"]').is_visible()
        creator.get_by_role("button", name="Создать активность").click()
        event = read_state()[0][-1]
        key = event["entity_id"]
        assert event["event_type"] == "activity.created"
        page.goto(URL + "/create-achievement/")
        creator = page.locator('form[action="/action/create-achievement/"]')
        creator.get_by_label("Название").fill("Первая прогулка")
        creator.locator('[name="condition_type"]').select_option("activity")
        creator.locator('[name="activity"]').select_option(key)
        creator.get_by_label("Порог").fill("1")
        creator.get_by_label("Описание условия получения").fill(
            "Прогуляться один раз"
        )
        creator.get_by_role("button", name="Создать достижение").click()
        page.goto(URL + "/create-quest/")
        creator = page.locator('form[action="/action/create-quest/"]')
        creator.get_by_label("Название").fill("Выйти в парк")
        creator.get_by_label("Активность").select_option(key)
        creator.get_by_label("Число выполнений").fill("1")
        creator.get_by_label("Описание условия выполнения").fill(
            "Выйти на прогулку"
        )
        creator.get_by_role("button", name="Создать задание").click()
        page.goto(URL + "/activities/")
        page.get_by_label("Поиск по названию").fill("Прогулка в парке")
        visible = page.locator(".activity-tile:visible")
        assert visible.count() == 1
        visible.get_by_role(
            "button", name="Закрепить Прогулка в парке"
        ).click()
        activity(page, key)
        page.goto(URL + "/achievements/")
        assert "Первая прогулка" in page.locator(
            ".achievement-card.unlocked"
        ).all_text_contents()[-1]
        page.goto(URL + "/quests/")
        custom_quest = page.locator(".quest-card").filter(
            has=page.get_by_role("heading", name="Выйти в парк")
        )
        assert custom_quest.get_by_text("Выполнено", exact=True).is_visible()
        page.goto(URL + "/developer/")
        page.get_by_role("button", name="Выйти из режима").click()
        activity(page, "meditation")
        events, state = read_state()
        assert events[-1]["payload"]["xp_by_stat"]["will"]["credited_xp"] == 30
        page.goto(URL + "/activities/")
        tile = page.locator('.activity-tile').filter(
            has=page.get_by_role(
                "heading", name="Работа над текстом", exact=True
            )
        )
        tile.get_by_role("button", name="Закрепить Работа над текстом",
                         exact=True).click()
        assert page.locator(
            '.activity-tile[data-search="работа над текстом"]'
        ).count() == 2
        page.get_by_label("Поиск по названию").fill("текст")
        assert page.locator(".activity-tile:visible").count() == 2
        page.locator('input[name="category-filter"][value="Тело"]').check()
        assert page.locator("#no-activities").is_visible()
        writing_category = page.locator(
            '.activity-tile[data-search="работа над текстом"]'
        ).first.get_attribute("data-category")
        page.get_by_label(writing_category, exact=True).check()
        assert page.locator(".activity-tile:visible").count() == 2
        page.locator(".activity-tile:visible").first.get_by_role(
            "link", name="Рассчитать", exact=True
        ).click()
        assert page.get_by_label("Активность").input_value() == "writing"
        page.get_by_label("День зачёта").select_option("previous")
        page.get_by_role("button", name="Рассчитать награду").click()
        assert "Выполнение №" not in page.locator(
            "#activity-calculator"
        ).inner_text()
        no_overflow(page)
        page.get_by_role("button", name="Подтвердить выполнение").click()
        page.wait_for_url("**/result/**")
        events, state = read_state()
        yesterday_event = events[-1]
        expected = services.day_choices(state["config"])["previous"]
        assert yesterday_event["game_date"] == expected.isoformat()
        page.goto(URL + "/settings/")
        page.get_by_label("Начало игрового дня").fill("05:00")
        page.get_by_role("button", name="Сохранить настройки").click()
        events, state = read_state()
        assert state["config"]["boundary_minutes"] == 300
        assert yesterday_event in events
        page.goto(URL + "/activities/")
        page.screenshot(path=ARTIFACTS / "activities-desktop.png",
                        full_page=True)
        before = state
        connections.close_all()
        stop_server(process)
        process = start_server()
        page.goto(URL)
        assert read_state()[1] == before
        page.screenshot(path=ARTIFACTS / "progress.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        for section in ("", "activities/", "skills/", "quests/", "display/",
                        "achievements/", "history/", "settings/"):
            page.goto(URL + "/" + section)
            no_overflow(page)
        page.goto(URL + "/activities/?activity=writing")
        page.screenshot(path=ARTIFACTS / "activities-mobile.png",
                        full_page=True)
        page.get_by_role("button", name="Рассчитать награду").click()
        no_overflow(page)
        page.screenshot(path=ARTIFACTS / "preview-mobile.png", full_page=True)
        page.goto(URL)
        page.screenshot(path=ARTIFACTS / "mobile.png", full_page=True)
        page.get_by_role("button", name="Открыть навигацию").click()
        page.get_by_role("navigation").get_by_role(
            "link", name="История", exact=True
        ).click()
        assert page.get_by_role(
            "heading", name="История", exact=True
        ).is_visible()
        read_state(
            advance=True,
            moment=services.local_now(state["config"]) + timedelta(days=40),
        )
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.goto(URL + "/reports/")
        assert page.locator(".report-link").count() > 1
        page.locator(".report-link").first.click()
        assert page.locator(".report-detail").is_visible()
        page.screenshot(path=ARTIFACTS / "report.png", full_page=True)
        before_tutorial = read_state()[1]
        page.goto(URL + "/settings/")
        page.get_by_role("button", name="Пройти обучение", exact=True).click()
        page.locator(".tutorial-guide").get_by_role(
            "button", name="Далее", exact=True
        ).click()
        page.locator(".tutorial-guide").get_by_role(
            "button", name="Пропустить", exact=True
        ).click()
        assert read_state()[1] == before_tutorial
        page.goto(URL + "/developer/")
        assert not page.get_by_role("link", name="Режим разработчика",
                                    exact=True).count()
        page.goto(URL + "/catalog/?kind=activity&key=" + key)
        assert page.get_by_role(
            "heading", name="Связанные задания — будут архивированы вместе"
        ).is_visible()
        page.get_by_role("link", name="Отмена", exact=True).click()
        assert key in services.catalog(read_state()[0])
        page.goto(URL + "/catalog/?kind=activity&key=" + key)
        page.get_by_role(
            "button", name="Архивировать вместе с зависимыми заданиями"
        ).click()
        assert key not in services.catalog(read_state()[0])
        page.goto(URL + "/history/")
        assert page.get_by_text("Прогулка в парке", exact=True).count() > 0
        page.goto(URL + "/elements/")
        page.get_by_label("Показывать режим разработчика").check()
        page.get_by_role("button", name="Сохранить отображение").click()
        page.goto(URL + "/developer/")
        page.get_by_role("button", name="Включить режим", exact=True).click()
        page.get_by_role("link", name="Архив", exact=True).click()
        page.locator(
            'input[name="targets"][value="activity:' + key + '"]'
        ).check()
        page.get_by_role("button", name="Разархивировать выбранное").click()
        assert key in services.catalog(read_state()[0])
        page.screenshot(path=ARTIFACTS / "archive.png", full_page=True)
        assert not errors, errors
        with services.journal_path().open("ab") as target:
            target.write(b'{"interrupted":')
        response = page.goto(URL)
        assert response.status == 503
        assert "прерванная запись" in page.locator("main").inner_text()
        page.get_by_role("button", name="Создать резервную копию").click()
        assert list((ARTIFACTS / "backups").glob("*.bak"))
        browser.close()
    (ARTIFACTS / "result.json").write_text(json.dumps({
        "status": "passed", "javascript_errors": errors,
        "checks": ["desktop", "mobile", "activity", "level", "achievement",
                   "writing", "correction", "perk", "restart", "reports",
                   "corrupt_tail", "backup"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Browser checks passed. Artifacts:", ARTIFACTS)
finally:
    stop_server(process)
    server_log.close()
