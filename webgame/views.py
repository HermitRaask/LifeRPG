"""HTTP handling only; mutations are journal-first application services."""
from functools import wraps
from urllib.parse import urlencode
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from liferpg.domain.achievements import achievement_names
from liferpg.domain.activities import catalog
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.content import CONTENT_COMMANDS, MODIFIERS, definitions
from liferpg.domain.perks import PERKS
from liferpg.domain.skills import active_root_skills
from liferpg.domain.quests import freeze_settings, quest_catalog
from liferpg.preferences import preferences, quick_activity
from liferpg.statistics import period_statistics
from webgame import forms, notifications, selectors, services
from webgame.models import Report

PAGES = {
    "overview": ("Обзор", "Каждое действие имеет значение."),
    "activities": ("Активности", "Небольшие шаги. Настоящий прогресс."),
    "stats": ("Характеристики", "Шесть направлений вашего развития."),
    "skills": ("Навыки", "От первого шага к мастерству."),
    "quests": ("Задания и серии", "Регулярность становится привычкой."),
    "achievements": ("Достижения", "Важные моменты вашего пути."),
    "history": ("История", "Ваши действия и фактические начисления."),
    "reports": ("Отчёты", "Посмотрите, как далеко вы продвинулись."),
    "settings": ("Настройки", "Ваш ритм. Ваш игровой день."),
    "developer": ("Режим разработчика", "Управление правилами и прогрессом."),
}
CREATE_ITEMS = {
    "create-activity": ("Активность", "Создать активность"),
    "create-achievement": ("Достижение", "Создать достижение"),
    "create-skill": ("Навык", "Создать навык"),
    "create-quest": ("Задание", "Создать задание"),
}
SUBPAGES = {
    "display": ("Отображение заданий", ""),
    "elements": ("Основные элементы", ""),
    "notifications": ("Все уведомления", ""),
    "developer-add": ("Добавить элемент", ""),
    "developer-edit": ("Изменить элемент", ""),
    **{key: (label[1], "") for key, label in CREATE_ITEMS.items()},
}
EDITOR_PAGES = {"developer-add", "developer-edit", *CREATE_ITEMS}

DEV_COMMANDS = {
    "perk", "level", "streak", "achievement", "batch", "rules",
    "freezes", "restore",
}


def initial_form(cls, **values):
    return cls(initial={"operation": str(uuid4()), **values})


def game_form(form, events):
    if "activity" in form.fields:
        form.fields["activity"].choices = [
            (key, item.name) for key, item in catalog(events).items()
        ]
    quests = quest_catalog(definitions(events, "quest"), events)
    if "streak" in form.fields:
        form.fields["streak"].choices = [
            (key, row[0]) for key, row in quests.items()
        ]
    if isinstance(form, forms.StreakForm):
        form.fields["key"].choices = [
            (key, row[0]) for key, row in quests.items()
        ]
    if isinstance(form, forms.AchievementForm):
        form.fields["key"].choices = list(achievement_names(events).items())
    if isinstance(form, forms.CreateAchievementForm):
        form.fields["skill"].choices = [
            ("", "—"), ("any", "Любой базовый навык"),
            *[(key, data["name"])
              for key, data in active_root_skills(events).items()],
        ]
    if isinstance(form, forms.CreateSkillForm):
        form.set_activities(catalog(events))
    if isinstance(form, forms.DescriptionForm):
        kind = (form.data.get("kind") if form.is_bound
                else form.initial.get("kind", "activity"))
        targets = {
            "activity": [(k, a.name) for k, a in catalog(events).items()],
            "quest": [(key, row[0]) for key, row in quests.items()],
            "achievement": list(achievement_names(events).items()),
            "perk": list(PERKS.items()),
            "modifier": [(k, row[0]) for k, row in MODIFIERS.items()],
        }
        form.fields["target"].choices = targets.get(kind, [])
    if isinstance(form, forms.DisplayForm):
        for period in ("daily", "weekly"):
            form.fields[period + "_pins"].choices = [
                (key, row[0]) for key, row in quests.items()
                if row[1] == period
            ]
    return form


def developer_enabled(request, display):
    return (display["show_developer"] and
            request.session.get("developer") == display["developer_revision"])


def overview_quests(rows, mode, pins, hide_done):
    selected = rows if mode == "all" else [q for q in rows if q["key"] in pins]
    visible = [q for q in selected if not (hide_done and q["done"])]
    if not rows:
        message = "Нет доступных заданий."
    elif not selected:
        message = "Нет закреплённых заданий."
    elif all(q["done"] for q in rows):
        message = "Все задания выполнены!"
    elif all(q["done"] for q in selected):
        message = "Закреплённые задания выполнены!"
    else:
        message = ""
    return {"rows": visible, "message": message,
            "done": sum(q["done"] for q in selected), "total": len(selected)}


def page_context(request, section, *, advance=False, bound_form=None,
                 source=None):
    pages = {**PAGES, **SUBPAGES}
    if section not in pages:
        raise Http404
    events, state = source or services.refresh(advance=advance)
    data = selectors.context(events, state)
    display = preferences(events, "display.updated")
    if source:
        display.update(show_developer=True)
    if section == "developer" and not display["show_developer"]:
        raise Http404
    developer = source is not None or developer_enabled(request, display)
    data["display"] = display
    history = notifications.history(events)
    data["notification_recent"] = history[:10]
    data["notification_page"] = Paginator(history, 30).get_page(
        request.GET.get("page")
    )
    pending = (set() if source else
               set(request.session.pop("notification_pending", [])))
    data["notification_toasts"] = [
        row for row in reversed(history) if row["id"] in pending
    ]
    data["all_perks"] = data["perks"]
    replaced = "victory" in state["perks"]
    by_key = {row["key"]: row for row in data["all_perks"]}
    data["perks"] = [
        by_key["victory"] if row["key"] == "lazy" and replaced else row
        for row in data["all_perks"] if row["key"] != "victory"
    ]
    data["desktop_available"] = (source is not None
                                 or hasattr(settings, "LIFERPG_APPLY_SYSTEM"))
    data["display_form"] = game_form(
        bound_form if (section in ("display", "elements")
                       and bound_form is not None)
        else initial_form(forms.DisplayForm, **display), events
    )
    data["freeze_forms"] = [
        initial_form(forms.FreezeForm, kind=kind,
                     stock=state["quests"]["banks"][kind],
                     **freeze_settings(state["quests"], kind))
        for kind in ("daily", "weekly")
    ]
    data["system_form"] = initial_form(
        forms.SystemForm, **preferences(events, "system.preferences")
    )
    for period in ("daily", "weekly"):
        data[period + "_overview"] = overview_quests(
            data[period], display[period + "_mode"], display[period + "_pins"],
            display["hide_done"],
        )
    data.update(
        section=section, title=pages[section][0], subtitle=pages[section][1],
        navigation=[(k, v[0]) for k, v in PAGES.items()
                    if k != "developer" or display["show_developer"]],
        developer=developer,
        activity_form=initial_form(
            forms.CompletionForm,
            activity=request.GET.get("activity", quick_activity(events)),
            intensity="2",
        ),
    )
    game_form(data["activity_form"], events)
    config = state["config"]
    data["settings_form"] = initial_form(
        forms.SettingsForm, timezone=config["timezone"],
        boundary=data["boundary_display"],
        backup_on_change=config["backup_on_change"],
    )
    choices = services.day_choices(config)
    data["activity_form"].fields["day_choice"].choices = [
        (key, f"{label} · {choices[key]:%d.%m.%Y}")
        for key, label in data["activity_form"].fields["day_choice"].choices
    ]
    pins = services.pinned_activities(events)
    for activity in data["activities"]:
        activity["pinned"] = activity["key"] in pins
        activity["pin_operation"] = str(uuid4())
    data["pinned"] = [a for a in data["activities"] if a["pinned"]]
    data["activities"].sort(key=lambda a: a["category"])
    data["categories"] = list(dict.fromkeys(
        a["category"] for a in data["activities"]
    ))
    data["batch_retry"] = None
    retry = None if source else request.session.get("batch_retry")
    if retry:
        data["batch_retry"] = game_form(forms.BatchForm(initial=retry), events)
    data["developer_forms"] = [

        ("batch", "Выполнить несколько раз", initial_form(
            forms.BatchForm, intensity="2", amount=1
        )),
        ("streak", "Установить длину серии", initial_form(forms.StreakForm)),
        ("level", "Установить уровень", initial_form(forms.LevelForm)),
        ("achievement", "Выдать достижение", initial_form(
            forms.AchievementForm
        )),
        ("rules", "Коэффициенты будущих начислений", initial_form(
            forms.RulesForm,
            intensity=" ".join(config["intensity"].values()),
            repetition=" ".join(config["repetition"]),
        )),
    ]
    if section == "developer-add":
        data["create_items"] = CREATE_ITEMS.items()
    if section in CREATE_ITEMS:
        data["command"] = section
        data["content_form"] = game_form(
            bound_form if bound_form is not None
            else initial_form(forms.COMMAND_FORMS[section]), events,
        )
    if section == "developer-edit":
        data["description_form"] = game_form(
            bound_form if bound_form is not None
            else initial_form(forms.DescriptionForm,
                              kind=request.GET.get("kind", "activity"),
                              target=request.GET.get("target", "")), events,
        )
        data["description_records"] = {
            "activity": [{**row, "details": "Базовый XP · " + row["rewards"]
                          + " · " + row["category"] + " · "
                          + (row["requirement"] or "Без условия")}
                         for row in data["activities"]],
            "quest": [{**row, "details": row["parameters"] + " · " + (
                "Ежедневное" if row["kind"] == "daily" else "Еженедельное"
            )} for row in data["quests"]],
            "achievement": [{**row, "details": row["parameters"]}
                            for row in data["achievements"]],
            "perk": [{**row, "condition_text": row["description"],
                      "details": row["rule"]} for row in data["all_perks"]],
            "modifier": [{**row, "condition_text": row["description"],
                          "details": row["rule"]}
                         for row in data["modifiers"]],
        }
        edit_form = data["description_form"]
        if not edit_form.is_bound:
            selected = next((
                row for row in data["description_records"].get(
                    edit_form.initial.get("kind", "activity"), []
                ) if row["key"] == edit_form.initial.get("target")
            ), None)
            if selected:
                edit_form.initial.update(
                    description=selected.get("description", ""),
                    condition_text=selected.get("condition_text", ""),
                )
    if data["developer"]:
        for perk in data["all_perks"]:
            perk["operation"] = str(uuid4())
    for command, _, form in data["developer_forms"]:
        game_form(form, events)
        form.auto_id = f"id_{command}_%s"
    if section == "history":
        data["history_page"] = Paginator(data["history"], 20).get_page(
            request.GET.get("page")
        )
        target = request.GET.get("edit")
        if target:
            row = next((h for h in data["history"]
                        if str(h["seq"]) == target), None)
            if row and row["editable"]:
                data["revision_form"] = initial_form(
                    forms.RevisionForm, target=row["seq"],
                    activity=row["activity"], intensity=row["intensity"],
                )
    if "revision_form" in data:
        game_form(data["revision_form"], events)
    if section == "reports":
        period_form = forms.PeriodForm(request.GET or None)
        data["period_form"] = period_form
        start = end = None
        valid = not request.GET or period_form.is_valid()
        if request.GET and valid:
            start = period_form.cleaned_data.get("start")
            end = period_form.cleaned_data.get("end")
        if valid:
            statistics = period_statistics(
                events, start.isoformat() if start else None,
                end.isoformat() if end else None,
            )
            data["statistics"] = selectors.statistics_labels(
                statistics, events
            )
            data["xp_summary"] = [
                (ATTRIBUTE_NAMES[k], v) for k, v in statistics["xp"].items()
            ]
        data["reports"] = ([] if source else
                           Report.objects.order_by("-end", "kind"))
        selected = request.GET.get("report")
        if selected and source is None:
            data["selected_report"] = Report.objects.filter(
                period=selected
            ).first()
            if data["selected_report"]:
                report = data["selected_report"]
                report.data = selectors.statistics_labels(
                    report.data, events
                )
    return data


def readable_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except (ValueError, OSError, DatabaseError) as exc:
            return render(request, "webgame/error.html",
                          {"error": str(exc)}, status=503)
    return wrapped


def page(request, section="overview"):
    if section == "perks":
        return redirect(reverse("page", args=["stats"]) + "#perks")
    try:
        data = page_context(
            request, section, advance=section in ("overview", "reports")
        )
        return render(request, "webgame/page.html", data)
    except (ValueError, OSError, DatabaseError) as exc:
        return render(request, "webgame/error.html", {"error": str(exc)},
                      status=503)


def preview_response(request, form, *, day=None, notice=None):
    data = page_context(request, "activities")
    game_form(form, data["events"])
    data["activity_form"] = form
    if form.is_valid():
        try:
            payload, day, token = services.quote(
                form.cleaned_data, form.cleaned_data["operation"], day=day
            )
            fields = form.data.copy()
            fields["quote"] = token
            data["activity_form"] = game_form(
                forms.CompletionForm(fields), data["events"]
            )
            data["preview_skill_rewards"] = [
                (item["name"], item["links"][payload["activity_id"]])
                for item in active_root_skills(data["events"]).values()
                if payload["activity_id"] in item["links"]
            ]
            data.update(preview_payload=payload, selected_day=day,
                        preview_rows=selectors.reward_rows(payload),
                        preview_notice=notice)
        except (ValueError, OSError) as exc:
            form.add_error(None, str(exc))
    return render(request, "webgame/page.html", data,
                  status=200 if not form.errors else 400)


@require_POST
@readable_errors
def preview(request):
    return preview_response(request, forms.CompletionForm(request.POST))


def editor_forbidden(request):
    return render(request, "webgame/error.html", {
        "error": "Сначала включите режим разработчика.",
    }, status=403)


@require_POST
@readable_errors
def action(request, command):
    if command == "backup":
        try:
            path = services.backup()
            messages.success(request, f"Резервная копия сохранена: {path}")
        except (ValueError, OSError) as exc:
            messages.error(request, str(exc))
        return redirect("page", section="settings")
    events, _ = services.refresh()
    display = preferences(events, "display.updated")
    developer = developer_enabled(request, display)
    if command == "developer":
        if not display["show_developer"]:
            return HttpResponse(status=403)
        request.session.pop("developer", None)
        if request.POST.get("enabled") == "1":
            request.session["developer"] = display["developer_revision"]
        return redirect("page", section="developer")
    if command in DEV_COMMANDS and not developer:
        return editor_forbidden(request)
    cls = forms.COMMAND_FORMS.get(command)
    if cls is None:
        raise Http404
    submitted = request.POST.copy()
    if (command == "display"
            and submitted.get("panel") in ("quests", "elements")):
        panel = submitted.pop("panel")[0]
        fields = (("show_next", "show_perks",
                   "show_modifiers", "show_developer")
                  if panel == "quests" else
                  ("daily_mode", "weekly_mode", "daily_pins", "weekly_pins",
                   "hide_done"))
        for key in fields:
            value = display[key]
            if isinstance(value, list):
                submitted.setlist(key, value)
            elif isinstance(value, bool):
                submitted[key] = "on" if value else ""
            else:
                submitted[key] = value
    form = game_form(cls(submitted), events)
    if form.is_valid():
        values = form.cleaned_data.copy()
        operation = str(values.pop("operation"))
        if command in CONTENT_COMMANDS:
            values = form.definition()
        if command == "description":
            values = {"kind": values["kind"], "target": values["target"],
                      "texts": form.texts()}
        if command == "settings":
            boundary = values.pop("boundary")
            values["boundary_minutes"] = boundary.hour * 60 + boundary.minute
        try:
            if command == "complete":
                services.verified_quote(values, operation)
            notification_before = {
                row["id"] for row in notifications.history(events)
            }
            result = services.execute(
                command, values, operation, developer=developer,
                developer_revision=request.session.get("developer"),
            )
            after_events, _ = services.refresh()
            fresh = [row["id"] for row in notifications.history(after_events)
                     if row["id"] not in notification_before]
            request.session["notification_pending"] = list(dict.fromkeys(
                request.session.get("notification_pending", []) + fresh
            ))
            if result and result.get("batch"):
                request.session["batch_result"] = result
                request.session["batch_retry"] = (
                    {"operation": operation, **values}
                    if result["error"] else None
                )
                return redirect("page", section="developer")
            if command in ("complete", "revise"):
                return redirect("result", operation=operation)
            if command == "perk":
                return redirect(reverse("page", args=["stats"]) + "#perks")
            if command == "display" and not values["show_developer"]:
                request.session.pop("developer", None)
            if command == "quick-activity":
                return HttpResponse(status=204)
            if command == "display":
                messages.success(request, "Настройки отображения сохранены.")
                return redirect("page", section=(
                    "elements" if request.POST.get("panel") == "elements"
                    else "display"
                ))
            if command == "description":
                messages.success(request, "Описание сохранено.")
                return redirect(reverse("page", args=["developer-edit"])
                                + "?" + urlencode({
                                    "kind": values["kind"],
                                    "target": values["target"],
                                }))
            if command in CREATE_ITEMS:
                messages.success(request, "Элемент создан.")
                return redirect("page", section="developer-add")
            if command == "pin":
                return redirect("page", section="activities")
            messages.success(request, "Изменение сохранено.")
            return redirect("page", section=(
                "developer" if command in DEV_COMMANDS else "settings"
            ))
        except services.PreviewChanged as exc:
            return preview_response(
                request, form, day=exc.day, notice=str(exc)
            )
        except (ValueError, OSError, DatabaseError) as exc:
            form.add_error(None, str(exc))
    section = "developer-edit" if command == "description" else command
    if section in SUBPAGES:
        data = page_context(request, section, bound_form=form)
        return render(request, "webgame/page.html", data, status=400)
    return render(request, "webgame/form_error.html", {
        "form": form, "command": command,
    }, status=400)


@readable_errors
def result(request, operation):
    data = page_context(request, "overview")
    event = next((e for e in data["events"]
                  if e["event_id"] == str(operation)), None)
    if event is None:
        raise Http404
    data["result_event"] = event
    data["result_rows"] = selectors.reward_rows(event["payload"])
    data["result_messages"] = selectors.effect_messages(
        event["payload"], developer=data["developer"], events=data["events"]
    )
    return render(request, "webgame/page.html", data)
