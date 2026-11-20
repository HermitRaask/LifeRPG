"""Guided read-only demo, separated from the personal journal and SQLite."""
from copy import deepcopy
from uuid import uuid4

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from liferpg.domain.activities import catalog
from liferpg.domain.achievements import evaluate_achievements
from liferpg.domain.catalog_state import configured, tutorial_state
from liferpg.domain.content import definitions
from liferpg.domain.perks import active_perks
from liferpg.domain.skills import active_root_skills
from liferpg.preferences import preferences
from liferpg.domain.quests import (
    advance, evaluate, quest_state, quest_catalog,
)
from liferpg.domain.skills import award_root_skills
from liferpg.game import completion_payload, make_event, project
from liferpg.journal import read_events
from webgame import services

STEPS = [
    ("overview", ".stats-grid", "Характеристики",
     "Активности дают опыт шести характеристикам. Здесь видны уровни и XP."),
    ("overview", ".dashboard-grid", "Задания",
     "Ежедневные и еженедельные задания помогают поддерживать ритм."),
    ("activities", ".category-filters", "Поиск и категории",
     "Поиск по названию и фильтры категорий работают вместе."),
    ("activities", ".catalog-group", "Закреплённые",
     "Закрепляйте нужные активности: они остаются и в своей категории."),
    ("activities", ".activity-tile", "Расчёт активности",
     ('Кнопка «Рассчитать» открывает параметры и день '
         'зачёта. XP даётся после подтверждения.')),
    ("stats", ".root-skills", "Базовые навыки",
     "Здесь виден прогресс навыков до 15-го уровня и мастерства."),
    ("stats", "#perks", "Перки",
     "Перки меняют награды по указанным правилам."),
    ("stats", "#modifiers", "Модификаторы",
     "Модификаторы описывают состояние персонажа и связанные эффекты."),
    ("skills", ".skill-card", "Развитие навыков",
     "Связанные активности начисляют XP навыку."),
    ("skills", ".skill-card .skill-mastery", "Мастерство",
     ("При повышении с 15-го уровня рост останавливается, "
      "а навык считается мастерски освоенным")),
    ("quests", ".bank-row", "Защита серии",
     "Сначала расходуются заморозки, затем один «Последний шанс» за серию."),
    ("quests", "section[aria-label]", "Задания и серии",
     ("Для выполнения задания нужно пройти соответствующую активность. "
      "Серия даёт бонус к получению опыта.")),
    ("achievements", ".achievement-card", "Достижения",
     "Здесь можно посмотреть важные вехи вашего пути"),
    ("history", ".panel", "История",
     ('Здесь подтверждённые действия и фактический '
         'опыт. Исправления доступны один час.')),
    ("reports", ".panel", "Отчёты",
     "Выберите период, чтобы увидеть действия, XP, уровни и прогресс."),
    ("settings", "#display-settings", "Отображение",
     ('В отдельных настройках выберите элементы обзора '
         'и закреплённые задания.')),
    ("settings", "#system-settings", "Системные настройки",
     "Настройка QoL-функций"),
    ("settings", "#game-time-settings", "Игровое время",
     ('Часовой пояс и граница суток определяют новые '
         'игровые даты. Старые даты сохраняются.')),
    ("settings", "#backup-settings", "Резервные копии",
     ('Копия журнала включает прогресс, каталог, '
         'настройки и архивные состояния.')),
    ("settings", "#content-editor", "Управление элементами",
     ("Создавайте, редактируйте и удаляйте различные элементы, "
      "такие как задания и активности.")),
    ("elements", ".element-toggle:last-of-type",
     "Отображение режима разработчика",
     "В «Основных элементах» можно включить пункт разработчика в навигации."),
    ("developer", ".developer-banner", "Режим разработчика",
     ('Отладочные инструменты меняют уровни, серии и '
         'правила. После обучения режим будет выключен.')),
    ("developer", "#developer-tools", "Инструменты разработчика",
     ('Здесь пакетные выполнения, ручные значения и '
         'архив. Демонстрация не меняет личные данные.')),
]


def demo_events(personal=()):
    """Build sample results in memory using the same gameplay calculations."""
    from liferpg.domain.configuration import configuration
    events = []
    moment = services.local_now(configuration(personal))
    # Copy definitions without personal XP or completion history.
    sample_catalog = catalog()
    for key, item in catalog(personal).items():
        if key in sample_catalog:
            continue
        definition = {
            "name": item.name, "description": item.description,
            "category": item.category, "rewards": item.rewards(),
            "condition": item.condition or {"type": "none"},
        }
        events.append(make_event(
            events, str(uuid4()), "activity.created", key, moment,
            {"xp_by_stat": {}, "definition": definition}, "Учебный каталог",
        ))
    personal_skills = active_root_skills(personal)
    for key, definition in personal_skills.items():
        if key == "writing":
            continue
        events.append(make_event(
            events, str(uuid4()), "skill.created", key, moment,
            {"xp_by_stat": {}, "definition": deepcopy(definition)},
            "Учебный каталог навыков",
        ))
    personal_quests = {
        key: row for key, row in definitions(personal, "quest").items()
        if key in quest_catalog(definitions(personal, "quest"), personal)
    }
    for key, definition in personal_quests.items():
        events.append(make_event(
            events, str(uuid4()), "quest.created", key, moment,
            {"xp_by_stat": {}, "definition": deepcopy(definition)},
            "Учебный каталог заданий",
        ))
    display = preferences(personal, "display.updated")
    events.append(make_event(
        events, str(uuid4()), "display.updated", "local-player", moment,
        {"xp_by_stat": {}, "preferences": {
            **display, "show_developer": True,
        }},
        "Учебные настройки отображения",
    ))
    # The personal time settings affect only this generated demonstration.
    events[-1]["payload"]["configuration_snapshot"] = configuration(personal)
    today = services.day_choices(configuration(events), moment)["current"]
    completions = []
    actions = ["writing", "eagle", "training", "walk", "meditation", "eagle"]
    for definition in personal_skills.values():
        linked = next((key for key in definition["links"]
                       if key in catalog(events)), None)
        if linked and linked not in actions:
            actions.append(linked)
    repeats = {}
    for key in actions:
        activity = catalog(events)[key]
        character = project(events)[0]
        repeats[key] = repeats.get(key, 0) + 1
        repeat = repeats[key]
        payload = completion_payload(
            activity, "2", repeat, character, active_perks(events)[0],
            config=configuration(events),
        )
        state = quest_state(events)
        custom = definitions(events, "quest")
        advance(state, today, custom, events=events)
        completions.append((today, key))
        payload["consequences"].extend(evaluate(
            state, today, completions, custom, events=events
        ))
        payload["quest_state"] = state
        award_root_skills(events, payload, key)
        evaluate_achievements(events, character, payload)
        events.append(make_event(
            events, str(uuid4()), "activity.completed", key, moment, payload,
            "Учебный пример: " + activity.name,
        ))
    return events


def welcome(request):
    events = read_events(services.journal_path())
    if configured(events):
        return redirect("home")
    tutorial = tutorial_state(events)
    if tutorial["active"]:
        return redirect("tutorial")
    return render(request, "webgame/onboarding.html", {
        "title": "Добро пожаловать", "choose": tutorial["finished"],
    })


def tutorial(request):
    from webgame.views import page_context
    events = read_events(services.journal_path())
    state = tutorial_state(events)
    if not state["active"]:
        return redirect("home" if configured(events) else "welcome")
    step = min(state["step"], len(STEPS) - 1)
    section, target, title, explanation = STEPS[step]
    samples = demo_events(events)
    data = page_context(
        request, section, source=(samples, services.snapshot(samples))
    )
    data.update(tutorial={
        "step": step + 1, "total": len(STEPS), "target": target,
        "title": title, "text": explanation, "last": step == len(STEPS) - 1,
    })
    data["notification_toasts"] = []
    return render(request, "webgame/page.html", data)


@require_POST
def control(request):
    events = read_events(services.journal_path())
    state = tutorial_state(events)
    move = request.POST.get("move")
    if move == "choose":
        if configured(events) or not state["finished"]:
            return HttpResponse(
                "Профиль уже настроен или обучение ещё не завершено.",
                status=400,
            )
        try:
            services.execute("profile", {"preset": request.POST.get("preset")},
                             str(uuid4()))
        except ValueError as exc:
            return HttpResponse(str(exc), status=400)
        return redirect("home")
    if move not in ("start", "next", "back", "skip", "finish"):
        return HttpResponse(status=400)
    if move not in ("start", "skip") and not state["active"]:
        return HttpResponse(status=400)
    step = 0 if move == "start" else state["step"]
    if move == "next":
        step = min(step + 1, len(STEPS) - 1)
    if move == "back":
        step = max(0, step - 1)
    finished = move in ("skip", "finish")
    services.execute("tutorial", {
        "active": not finished, "finished": finished, "step": step,
    }, str(uuid4()))
    if finished:
        request.session.pop("developer", None)
        messages.success(
            request, "Обучение завершено. Режим разработчика выключен."
        )
    return redirect("tutorial" if not finished else
                    ("home" if configured(events) else "welcome"))


class SetupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        if (getattr(settings, "ONBOARDING_ENABLED", True)
                and not request.path.startswith(("/static/", "/onboarding/",
                                                 "/tutorial/"))):
            try:
                events = read_events(services.journal_path())
            except (ValueError, OSError):
                return self.get_response(request)
            state = tutorial_state(events)
            if state["active"] or not configured(events):
                if request.method != "GET":
                    return HttpResponse(
                        "Сначала завершите обучение и выбор профиля.",
                        status=409,
                    )
                return redirect("tutorial" if state["active"] else "welcome")
        return self.get_response(request)
