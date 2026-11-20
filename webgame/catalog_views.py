"""Normal catalog management and developer-only archive recovery."""
from uuid import uuid4

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from liferpg.domain.catalog_state import archive_state
from liferpg.preferences import preferences
from webgame import services, notifications
from webgame.catalog_services import records, related
from webgame.views import developer_enabled, page_context, readable_errors

LABELS = {
    "activity": "Активность", "quest": "Задание", "skill": "Базовый навык",
}


@readable_errors
def listing(request, archived=False):
    events, _ = services.refresh()
    display = preferences(events, "display.updated")
    developer = developer_enabled(request, display)
    if archived and not developer:
        return HttpResponse("Сначала включите режим разработчика.", status=403)
    data = page_context(request, "settings")
    data.update(title="Архив" if archived else "Управление каталогом",
                archived=archived, operation=str(uuid4()))
    if archived:
        data["rows"] = [
            {**row, "token": kind + ":" + key, "label": LABELS[kind]}
            for (kind, key), row in archive_state(events).items()
            if row["archived"]
        ]
        data["rows"].sort(key=lambda row: row["seq"])
    else:
        data["rows"] = [
            {"kind": kind, "key": key, "name": item["name"],
             "label": LABELS[kind]}
            for (kind, key), item in records(events).items()
        ]
        target = (request.GET.get("kind"), request.GET.get("key"))
        if target in records(events):
            row = records(events)[target]
            tasks, skills, achievements = (related(events, target[1])
                                           if target[0] == "activity"
                                           else ([], [], []))
            data["selected"] = {
                "kind": target[0], "key": target[1], "name": row["name"],
                "tasks": [records(events)[t]["name"] for t in tasks],
                "skills": skills, "achievements": achievements,
            }
    return render(request, "webgame/catalog.html", data)


@require_POST
@readable_errors
def mutate(request, command):
    if command not in ("archive", "restore"):
        return HttpResponse(status=400)
    if request.POST.get("confirmed") != "yes":
        return HttpResponse("Требуется явное подтверждение.", status=400)
    events, _ = services.refresh()
    display = preferences(events, "display.updated")
    developer = developer_enabled(request, display)
    if command == "restore" and not developer:
        return HttpResponse("Сначала включите режим разработчика.", status=403)
    values = (dict(kind=request.POST.get("kind"), key=request.POST.get("key"))
              if command == "archive" else
              {"targets": request.POST.getlist("targets")})
    notification_before = {r["id"] for r in notifications.history(events)}
    try:
        services.execute(
            command, values, request.POST.get("operation"),
            developer=developer,
            developer_revision=request.session.get("developer"),
        )
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc))
        return redirect("archive" if command == "restore" else "catalog")
    after_events, _ = services.refresh()
    fresh = [row["id"] for row in notifications.history(after_events)
             if row["id"] not in notification_before]
    request.session["notification_pending"] = list(dict.fromkeys(
        request.session.get("notification_pending", []) + fresh
    ))
    message = ("Элементы восстановлены." if command == "restore" else
               "Элементы архивированы. Прогресс и история сохранены.")
    messages.success(request, message)
    return redirect("archive" if command == "restore" else "catalog")
