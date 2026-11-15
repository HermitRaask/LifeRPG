"""HTTP handling only; mutations are journal-first application services."""
from functools import wraps
from uuid import uuid4

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from liferpg.domain.activities import catalog
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.statistics import period_statistics
from webgame import forms, selectors, services
from webgame.models import Report

PAGES = {
    "overview": ("Обзор", "Каждое действие имеет значение."),
    "activities": ("Активности", "Небольшие шаги. Настоящий прогресс."),
    "stats": ("Характеристики", "Шесть направлений вашего развития."),
    "skills": ("Навыки", "От первого шага к мастерству."),
    "quests": ("Задания и серии", "Регулярность становится привычкой."),
    "perks": ("Перки", "Ваши особенности и их влияние."),
    "achievements": ("Достижения", "Важные моменты вашего пути."),
    "history": ("История", "Ваши действия и фактические начисления."),
    "reports": ("Отчёты", "Посмотрите, как далеко вы продвинулись."),
    "settings": ("Настройки", "Ваш ритм. Ваш игровой день."),
    "developer": ("Режим разработчика", "Управление правилами и прогрессом."),
}
DEV_COMMANDS = {"perk", "level", "streak", "achievement", "batch", "rules"}


def initial_form(cls, **values):
    return cls(initial={"operation": str(uuid4()), **values})


def page_context(request, section, *, advance=False):
    if section not in PAGES:
        raise Http404
    events, state = services.refresh(advance=advance)
    data = selectors.context(events, state)
    data.update(
        section=section, title=PAGES[section][0], subtitle=PAGES[section][1],
        navigation=[(k, v[0]) for k, v in PAGES.items()],
        developer=request.session.get("developer", False),
        activity_form=initial_form(
            forms.ActivityForm,
            activity=request.GET.get("activity", "meditation"), intensity="2",
        ),
    )
    config = state["config"]
    data["settings_form"] = initial_form(
        forms.SettingsForm, timezone=config["timezone"],
        boundary=data["boundary_display"],
        backup_on_change=config["backup_on_change"],
    )
    data["clock_locked"] = any(e["event_type"] == "activity.completed"
                               for e in events)
    if data["clock_locked"]:
        for field in ("timezone", "boundary"):
            data["settings_form"].fields[field].widget.attrs["readonly"] = True
    data["batch_retry"] = None
    retry = request.session.get("batch_retry")
    if retry:
        data["batch_retry"] = forms.BatchForm(initial=retry)
    data["developer_forms"] = [
        ("perk", "Переключить перк", initial_form(forms.PerkForm)),
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
    for command, _, form in data["developer_forms"]:
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
            data["statistics"] = selectors.statistics_labels(statistics)
            data["xp_summary"] = [
                (ATTRIBUTE_NAMES[k], v) for k, v in statistics["xp"].items()
            ]
        data["reports"] = Report.objects.order_by("-end", "kind")
        selected = request.GET.get("report")
        if selected:
            data["selected_report"] = Report.objects.filter(
                period=selected
            ).first()
            if data["selected_report"]:
                report = data["selected_report"]
                report.data = selectors.statistics_labels(report.data)
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
    try:
        data = page_context(
            request, section, advance=section in ("overview", "reports")
        )
        return render(request, "webgame/page.html", data)
    except (ValueError, OSError, DatabaseError) as exc:
        return render(request, "webgame/error.html", {"error": str(exc)},
                      status=503)


@require_POST
@readable_errors
def preview(request):
    form = forms.ActivityForm(request.POST)
    data = page_context(request, "activities")
    data["activity_form"] = form
    if form.is_valid():
        try:
            payload = services.preview(
                catalog()[form.cleaned_data["activity"]],
                form.cleaned_data["intensity"],
            )
            data["preview_payload"] = payload
            data["preview_rows"] = selectors.reward_rows(payload)
        except (ValueError, OSError) as exc:
            form.add_error(None, str(exc))
    return render(request, "webgame/page.html", data,
                  status=200 if not form.errors else 400)


@require_POST
def action(request, command):
    developer = request.session.get("developer", False)
    if command == "developer":
        request.session["developer"] = request.POST.get("enabled") == "1"
        return redirect("page", section="developer")
    if command == "backup":
        try:
            path = services.backup()
            messages.success(request, f"Резервная копия сохранена: {path}")
        except (ValueError, OSError) as exc:
            messages.error(request, str(exc))
        return redirect("page", section="settings")
    if command in DEV_COMMANDS and not developer:
        return render(request, "webgame/error.html", {
            "error": "Сначала включите режим разработчика.",
        }, status=403)
    cls = forms.COMMAND_FORMS.get(command)
    if cls is None:
        raise Http404
    form = cls(request.POST)
    if form.is_valid():
        values = form.cleaned_data.copy()
        operation = str(values.pop("operation"))
        if command == "settings":
            boundary = values.pop("boundary")
            values["boundary_minutes"] = boundary.hour * 60 + boundary.minute
        try:
            result = services.execute(
                command, values, operation, developer=developer
            )
            if result and result.get("batch"):
                request.session["batch_result"] = result
                request.session["batch_retry"] = (
                    {"operation": operation, **values}
                    if result["error"] else None
                )
                return redirect("page", section="developer")
            if command in ("complete", "revise"):
                return redirect("result", operation=operation)
            messages.success(request, "Изменение сохранено.")
            return redirect("page", section=(
                "developer" if command in DEV_COMMANDS else "settings"
            ))
        except (ValueError, OSError, DatabaseError) as exc:
            form.add_error(None, str(exc))
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
    data["result_messages"] = selectors.effect_messages(event["payload"])
    return render(request, "webgame/page.html", data)
