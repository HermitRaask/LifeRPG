"""Run via manage.py test webgame; isolated database and journal."""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings

from liferpg.domain.activities import catalog
from liferpg.game import complete, project
from liferpg.journal import read_events
from webgame import services
from webgame.forms import RulesForm
from webgame.models import GameState, RecordedEvent, Report


def set_display(client, **values):
    return client.post("/action/display/", {
        "operation": str(uuid4()), "daily_mode": "all", "weekly_mode": "all",
        **values,
    })


def enable_developer(client):
    set_display(client, show_developer="on", hide_done="on")
    return client.post("/action/developer/", {"enabled": "1"})


class WebTests(TestCase):
    def setUp(self):
        folder = settings.BASE_DIR / ".venv"
        folder.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(prefix="web-test-", dir=folder)
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(DATA_DIR=Path(self.temp.name),
                                          ONBOARDING_ENABLED=False)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.client = Client()

    def perform(self, activity="meditation", operation=None):
        return services.execute("complete", {
            "activity": activity, "intensity": "2"
        }, operation or str(uuid4()))

    def test_new_save_and_all_pages(self):
        for page in ("", "activities/", "stats/", "skills/", "quests/",
                     "achievements/", "history/", "reports/",
                     "settings/"):
            response = self.client.get("/" + page)
            self.assertEqual(response.status_code, 200, page)
        self.assertEqual(self.client.get("/developer/").status_code, 404)
        state = GameState.objects.get().data
        self.assertEqual(state["stats"]["will"], {
            "level": 1, "xp": 0, "debt": 0,
        })
        self.assertIn("lazy", state["perks"])
        self.assertNotIn("developer", self.client.session)
        self.assertEqual(read_events(services.journal_path()), [])

    def test_preview_http_completion_retry_and_restart(self):
        data = {"operation": str(uuid4()), "activity": "meditation",
                "intensity": "2"}
        response = self.client.post("/preview/", data)
        self.assertContains(response, "+62 XP")
        self.assertEqual(read_events(services.journal_path()), [])
        data = response.context["activity_form"].data.dict()
        response = self.client.post("/action/complete/", data)
        self.assertEqual(response.status_code, 302)
        self.assertContains(
            self.client.get(response.url), "Действие сохранено"
        )
        original = GameState.objects.get().data
        self.client.post("/action/complete/", data)
        self.assertEqual(GameState.objects.get().data, original)
        response = self.client.post("/preview/", {
            **data, "operation": str(uuid4())
        })
        self.client.post("/action/complete/",
                         response.context["activity_form"].data.dict())
        before = GameState.objects.get().data
        self.assertEqual(before["stats"]["will"]["level"], 2)
        self.assertIn("first-level", before["achievements"])
        GameState.objects.all().delete()
        RecordedEvent.objects.all().delete()
        self.assertEqual(services.refresh()[1], before)

    def test_db_failure_after_durable_event_recovers_once(self):
        services.refresh()
        op = str(uuid4())
        original = services.synchronize
        calls = 0

        def interrupted():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DatabaseError("simulated crash after fsync")
            return original()

        with patch("webgame.services.synchronize", side_effect=interrupted):
            with self.assertRaises(DatabaseError):
                self.perform(operation=op)
        state = GameState.objects.get().data
        self.assertEqual(state["stats"]["will"]["xp"], 0)
        self.assertEqual(len(read_events(services.journal_path())), 2)
        self.perform(operation=op)
        state = GameState.objects.get().data
        self.assertEqual(state["stats"]["will"]["xp"], 62)
        self.assertEqual(RecordedEvent.objects.count(), 2)

    def test_sqlite_transaction_rolls_back_projection(self):
        self.perform()
        old = RecordedEvent.objects.count()
        complete(services.journal_path(), catalog()["writing"], "2",
                 str(uuid4()))
        with patch.object(GameState.objects, "update_or_create",
                          side_effect=DatabaseError("projection failed")):
            with self.assertRaises(DatabaseError):
                services.refresh()
        self.assertEqual(RecordedEvent.objects.count(), old)
        self.assertEqual(services.refresh()[1]["writing"]["xp"], 50)

    def test_invalid_input_csrf_and_developer_guards(self):
        response = self.client.post("/action/complete/", {
            "operation": str(uuid4()), "activity": "unknown", "intensity": "2"
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(read_events(services.journal_path()), [])
        csrf = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post("/action/developer/", {
            "enabled": "1"
        }).status_code, 403)
        for command in ("perk", "batch", "streak", "level",
                        "achievement", "rules"):
            self.assertEqual(self.client.post(
                f"/action/{command}/", {}
            ).status_code, 403)
            with self.assertRaisesRegex(ValueError, "разработчика"):
                services.execute(command, {}, str(uuid4()))
        self.assertEqual(read_events(services.journal_path()), [])

    def test_developer_changes_persist(self):
        enable_developer(self.client)
        commands = [
            ("perk", {"perk": "lazy"}),
            ("level", {"key": "strength", "value": 25}),
            ("level", {"key": "player", "value": 50}),
            ("streak", {"key": "writing", "value": 80}),
            ("achievement", {"key": "player-fifty"}),
        ]
        for command, values in commands:
            response = self.client.post(f"/action/{command}/", {
                "operation": str(uuid4()), **values
            })
            self.assertEqual(response.status_code, 302)
        state = services.refresh()[1]
        self.assertEqual(state["player_level"], 50)
        self.assertEqual(state["quests"]["streaks"]["writing"]["duration"], 80)
        self.assertEqual(state["stats"]["strength"]["level"], 25)
        self.assertNotIn("lazy", state["perks"])
        self.client.post("/action/developer/", {"enabled": "0"})
        event = self.perform()
        self.assertEqual(event["payload"]["xp_by_stat"]["will"]["credited_xp"],
                         50)
        before = len(read_events(services.journal_path()))
        services.execute("achievement", {"key": "player-fifty"},
                         str(uuid4()), developer=True)
        self.assertEqual(len(read_events(services.journal_path())), before)
        self.assertEqual(services.refresh()[1]["perks"],
                         GameState.objects.get().data["perks"])

    def test_batch_equivalence_and_retry(self):
        values = {"activity": "writing", "intensity": "2", "amount": 6}
        op = str(uuid4())
        result = services.execute("batch", values, op, developer=True)
        self.assertEqual(result["done"], 6)
        before = services.refresh()[1]
        services.execute("batch", values, op, developer=True)
        self.assertEqual(services.refresh()[1], before)
        manual = Path(self.temp.name) / "manual.jsonl"
        events = read_events(services.journal_path())
        moment = datetime.fromisoformat(events[1]["local_datetime"])
        for _ in range(6):
            complete(manual, catalog()["writing"], "2", str(uuid4()), moment)
        self.assertEqual(services.snapshot(read_events(manual)), before)

    def test_batch_partial_retry(self):
        values = {"activity": "writing", "intensity": "2", "amount": 4}
        op = str(uuid4())
        real_complete = services.complete
        calls = 0

        def broken(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("interrupted")
            return real_complete(*args, **kwargs)

        with patch("webgame.services.complete", side_effect=broken):
            result = services.execute("batch", values, op, developer=True)
        self.assertEqual(result["done"], 2)
        result = services.execute("batch", values, op, developer=True)
        self.assertEqual(result["done"], 4)
        self.assertEqual(services.refresh()[1]["writing"]["xp"], 200)

    def test_rules_future_only_preview_and_id_conflict(self):
        original = self.perform()
        form = RulesForm({
            "operation": str(uuid4()), "intensity": "0.5 2 3",
            "repetition": "1 1 .6 .3 .15",
        })
        self.assertTrue(form.is_valid(), form.errors)
        values = form.cleaned_data.copy()
        op = str(values.pop("operation"))
        services.execute("rules", values, op, developer=True)
        prediction = services.preview(catalog()["meditation"], "2")
        new = self.perform()
        self.assertEqual(prediction["xp_by_stat"],
                         new["payload"]["xp_by_stat"])
        self.assertEqual(new["payload"]["xp_by_stat"]["will"]["credited_xp"],
                         125)
        self.assertGreater(new["rules_version"], original["rules_version"])
        self.assertEqual(read_events(services.journal_path())[1], original)
        with self.assertRaises(ValueError):
            self.perform("writing", original["event_id"])

    def test_time_boundary_and_settings_change(self):
        services.execute("settings", {
            "timezone": "Europe/Moscow", "boundary_minutes": 300,
            "backup_on_change": True,
        }, str(uuid4()))
        moment = datetime(2026, 10, 5, 4, 59, tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            first = self.perform()
        with patch("webgame.services.local_now",
                   return_value=moment + timedelta(minutes=1)):
            second = self.perform()
        self.assertEqual(first["game_date"], "2026-10-04")
        self.assertEqual(second["game_date"], "2026-10-05")
        self.assertEqual(second["payload"]["occurrence_index_in_game_day"], 1)
        services.execute("settings", {
            "timezone": "UTC", "boundary_minutes": 240,
            "backup_on_change": False,
        }, str(uuid4()))
        self.assertTrue(list((Path(self.temp.name) / "backups").glob("*.bak")))

    def test_edit_window_cancel_and_read_only_history(self):
        original = self.perform()
        recorded = datetime.fromisoformat(original["recorded_at_utc"])
        values = {"target": original["seq"], "activity": "meditation",
                  "intensity": "2", "cancel": True}
        with patch("webgame.services.local_now",
                   return_value=recorded + timedelta(minutes=59, seconds=59)):
            result = services.execute("revise", values, str(uuid4()))
        self.assertEqual(result["event_type"], "correction.applied")
        self.assertEqual(services.refresh()[1]["stats"]["will"]["xp"], 0)
        before = services.journal_path().read_bytes()
        self.client.get("/history/")
        self.client.get("/history/")
        self.assertEqual(services.journal_path().read_bytes(), before)
        with patch("webgame.services.local_now",
                   return_value=recorded + timedelta(hours=1)):
            with self.assertRaisesRegex(ValueError, "первый час"):
                services.execute("revise", {**values, "cancel": False},
                                 str(uuid4()))
        self.assertEqual(services.journal_path().read_bytes(), before)

    def test_reports_catchup_boundaries_and_restore(self):
        moment = datetime(2026, 10, 31, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            self.perform()
        services.refresh(advance=True, moment=datetime(
            2026, 11, 1, 3, 59, tzinfo=moment.tzinfo
        ))
        self.assertEqual(Report.objects.count(), 0)
        services.refresh(advance=True, moment=datetime(
            2026, 11, 1, 4, 0, tzinfo=moment.tzinfo
        ))
        monthly = Report.objects.get(kind="monthly")
        self.assertEqual(monthly.data["xp"]["will"], 62)
        services.refresh(advance=True, moment=datetime(
            2026, 11, 16, 4, 0, tzinfo=moment.tzinfo
        ))
        self.assertEqual(Report.objects.filter(kind="weekly").count(), 3)
        expected = list(Report.objects.order_by("period").values())
        before = services.journal_path().read_bytes()
        services.refresh(advance=True, moment=datetime(
            2026, 11, 16, 4, 0, tzinfo=moment.tzinfo
        ))
        self.assertEqual(services.journal_path().read_bytes(), before)
        Report.objects.all().delete()
        services.refresh()
        self.assertEqual(list(Report.objects.order_by("period").values()),
                         expected)
        self.assertEqual(self.client.get("/reports/").status_code, 200)

    def test_corrupt_tail_diagnostic_and_raw_backup(self):
        self.perform()
        with services.journal_path().open("ab") as target:
            target.write(b'{"incomplete":')
        original = services.journal_path().read_bytes()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "строка 3", status_code=503)
        destination = services.backup()
        self.assertEqual(destination.read_bytes(), original)
        self.assertEqual(services.journal_path().read_bytes(), original)

    def test_recorded_results_are_not_recalculated(self):
        self.perform("writing")
        events = read_events(services.journal_path())
        character, _ = project(events)
        with patch("liferpg.game.completion_payload",
                   side_effect=AssertionError("must not recalculate")):
            state = services.refresh()[1]
        self.assertEqual(state["stats"]["creativity"]["xp"],
                         character.attribute("creativity").current_xp)
        self.assertEqual(state["writing"]["xp"], 50)

    def test_config_validation_and_clock_stays_unchanged(self):
        invalid = [
            ("settings", {"timezone": "Unknown/Zone", "boundary": "04:00"}),
            ("settings", {"timezone": "UTC", "boundary": "25:00"}),
            ("complete", {"activity": "meditation", "intensity": "99"}),
        ]
        for command, data in invalid:
            response = self.client.post(f"/action/{command}/", {
                "operation": str(uuid4()), **data,
            })
            self.assertEqual(response.status_code, 400)
            self.assertEqual(read_events(services.journal_path()), [])
        enable_developer(self.client)
        for factors in ("1 1", "1 0 -1", "1 nan 1", "1 1/0 1"):
            response = self.client.post("/action/rules/", {
                "operation": str(uuid4()), "intensity": factors,
                "repetition": "1 1 .6 .3 .15",
            })
            self.assertEqual(response.status_code, 400)
        self.assertEqual(len(read_events(services.journal_path())), 1)

    def test_manual_streak_increments_once_and_preserves_protection(self):
        services.execute("streak", {"key": "writing", "value": 10},
                         str(uuid4()), developer=True)
        before = services.refresh()[1]["quests"]
        self.perform("writing")
        after = services.refresh()[1]["quests"]
        self.assertEqual(after["streaks"]["writing"]["duration"], 11)
        self.assertEqual(after["streaks"]["writing"]["bonus_periods"], 11)
        self.assertEqual(
            after["streaks"]["writing"]["last_chance_used"],
            before["streaks"]["writing"]["last_chance_used"],
        )

    def test_old_rules_replay_in_new_database(self):
        self.perform("writing")
        self.perform()
        events = read_events(services.journal_path())
        expected = services.snapshot(events)
        for event in events:
            event["rules_version"] = 3
        import json
        services.journal_path().write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
            encoding="utf-8",
        )
        GameState.objects.all().delete()
        RecordedEvent.objects.all().delete()
        self.assertEqual(services.refresh()[1], expected)
        new = self.perform()
        self.assertEqual(new["rules_version"], 7)
        self.assertEqual(read_events(services.journal_path())[1], events[1])

    def test_edit_decreases_level_and_duplicate_does_not_reapply(self):
        first = self.perform()
        self.perform()
        values = {"target": first["seq"], "activity": "meditation",
                  "intensity": "2", "cancel": True}
        operation = str(uuid4())
        services.execute("revise", values, operation)
        response = self.client.get(f"/result/{operation}/")
        self.assertContains(response, "-62 XP")
        state = services.refresh()[1]
        self.assertEqual(state["stats"]["will"]["level"], 1)
        self.assertEqual(state["stats"]["will"]["xp"], 62)
        services.execute("revise", values, operation)
        self.assertEqual(services.refresh()[1], state)

    def test_web_metadata_tracks_developer_and_timezone(self):
        services.execute("settings", {
            "timezone": "America/New_York", "boundary_minutes": 240,
            "backup_on_change": False,
        }, str(uuid4()))
        services.execute("perk", {"perk": "lazy", "enabled": False},
                         str(uuid4()), developer=True)
        last = read_events(services.journal_path())[-1]
        self.assertEqual(last["actor"], "developer")
        self.assertEqual(last["timezone"], "America/New_York")
        self.assertEqual(last["payload"]["web_request"]["command"], "perk")

    def test_report_inclusive_range_and_no_xp_from_achievement(self):
        moment = datetime(2026, 10, 31, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            first = self.perform()
        later = moment + timedelta(days=1)
        with patch("webgame.services.local_now", return_value=later):
            self.perform()
        from webgame.services import report_data
        events = read_events(services.journal_path())
        start = moment.date()
        report = report_data(events, start, start)
        self.assertEqual(report["xp"]["will"], 62)
        self.assertEqual(len(report["history"]), 1)
        self.assertEqual(report["achievements"], [])
        row = first["payload"]["xp_by_stat"]["will"]
        self.assertEqual(row["xp_after"], 62)

    def test_malformed_settings_line_has_diagnostic(self):
        services.execute("settings", {
            "timezone": "UTC", "boundary_minutes": 240,
            "backup_on_change": False,
        }, str(uuid4()))
        events = read_events(services.journal_path())
        del events[0]["payload"]["after"]
        import json
        services.journal_path().write_text(
            json.dumps(events[0], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "строка 1", status_code=503)


class ActivityDayTests(TestCase):
    setUp = WebTests.setUp
    perform = WebTests.perform

    def preview_data(self, choice="current", activity="meditation"):
        response = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": activity,
            "intensity": "2", "day_choice": choice,
        })
        self.assertEqual(response.status_code, 200)
        return response, response.context["activity_form"].data.dict()

    def test_choices_before_boundary(self):
        zone = ZoneInfo("Europe/Moscow")
        config = {"boundary_minutes": 360}
        checks = [(2, 42, ("2026-11-15", "2026-11-14")),
                  (6, 0, ("2026-11-16", "2026-11-15"))]
        for hour, minute, expected in checks:
            moment = datetime(2026, 11, 16, hour, minute, tzinfo=zone)
            choices = services.day_choices(config, moment)
            self.assertEqual(tuple(map(str, choices.values())), expected)

    def test_selected_day_repeats_quests_statistics_and_replay(self):
        from liferpg.statistics import period_statistics
        moment = datetime(2026, 11, 15, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            # Replay a completion from the release that allowed tomorrow.
            complete(services.journal_path(), catalog()["writing"], "2",
                     str(uuid4()), moment,
                     credited_day=moment.date() + timedelta(days=1))
            for _ in range(2):
                _, data = self.preview_data("previous", "writing")
                response = self.client.post("/action/complete/", data)
                self.assertEqual(response.status_code, 302)
            events, state = services.refresh()
            done = [e for e in events
                    if e["event_type"] == "activity.completed"]
            self.assertEqual([e["game_date"] for e in done],
                             ["2026-11-16", "2026-11-14", "2026-11-14"])
            self.assertEqual([e["payload"]["occurrence_index_in_game_day"]
                              for e in done], [1, 1, 2])
            self.assertTrue(all(e["real_local_date"] == "2026-11-15"
                                for e in done))
            periods = state["quests"]["completed_periods"]["writing"]
            self.assertEqual(periods, ["2026-11-14", "2026-11-16"])
            stats = period_statistics(events, "2026-11-14", "2026-11-14")
            self.assertEqual(len(stats["history"]), 2)
            self.assertEqual(stats["skills"]["writing"], 100)
            GameState.objects.all().delete()
            RecordedEvent.objects.all().delete()
            self.assertEqual(services.refresh()[1], state)
            history = self.client.get("/history/")
            self.assertContains(history, "2026-11-14")
            self.assertContains(history, "2026-11-16")

    def test_midnight_and_boundary_change_keep_preview_date(self):
        moment = datetime(2026, 11, 15, 23, 59,
                          tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            _, data = self.preview_data("previous")
        with patch("webgame.services.local_now",
                   return_value=moment + timedelta(minutes=2)):
            services.execute("settings", {
                "timezone": "Europe/Moscow", "boundary_minutes": 60,
                "backup_on_change": False,
            }, str(uuid4()))
            response = self.client.post("/action/complete/", data)
            self.assertEqual(response.status_code, 302)
        event = read_events(services.journal_path())[-1]
        self.assertEqual(event["game_date"], "2026-11-14")
        self.assertEqual(event["real_local_date"], "2026-11-16")
        self.assertEqual(event["local_datetime"],
                         (moment + timedelta(minutes=2)).isoformat())

    def test_changed_reward_requires_another_confirmation(self):
        _, data = self.preview_data()
        self.perform()
        before = services.journal_path().read_bytes()
        response = self.client.post("/action/complete/", data)
        self.assertContains(response, "Расчёт изменился")
        self.assertEqual(services.journal_path().read_bytes(), before)
        updated = response.context["activity_form"].data.dict()
        self.assertEqual(self.client.post("/action/complete/", updated)
                         .status_code, 302)
        before = services.journal_path().read_bytes()
        self.assertEqual(self.client.post("/action/complete/", updated)
                         .status_code, 302)
        self.assertEqual(services.journal_path().read_bytes(), before)

    def test_invalid_selection_and_tampered_confirmation(self):
        response = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": "writing",
            "intensity": "2", "day_choice": "last-year",
        })
        self.assertEqual(response.status_code, 400)
        _, data = self.preview_data()
        for changed in ({"quote": "bad"}, {"day_choice": "tomorrow"},
                        {"intensity": "3"}, {"quote": ""}):
            self.assertEqual(self.client.post("/action/complete/", {
                **data, **changed,
            }).status_code, 400)
        self.assertEqual(read_events(services.journal_path()), [])

    def test_boundary_after_completion_preserves_history(self):
        moment = datetime(2026, 11, 15, 4, 30,
                          tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            first = self.perform()
            services.execute("settings", {
                "timezone": "Europe/Moscow", "boundary_minutes": 300,
                "backup_on_change": False,
            }, str(uuid4()))
            response, data = self.preview_data()
            self.assertEqual(str(response.context["selected_day"]),
                             "2026-11-14")
            self.client.post("/action/complete/", data)
            second = read_events(services.journal_path())[-1]
            services.execute("settings", {
                "timezone": "Europe/Moscow", "boundary_minutes": 240,
                "backup_on_change": False,
            }, str(uuid4()))
            state = services.refresh()[1]
            GameState.objects.all().delete()
            events, replayed = services.refresh()
            self.assertEqual(state, replayed)
            self.assertIn(first, events)
            self.assertIn(second, events)
            self.assertEqual(second["game_date"], "2026-11-14")
            self.assertEqual(first["game_date"], "2026-11-15")
            self.assertNotContains(self.client.get("/settings/"), "readonly")

    def test_pin_and_unpin_survive_projection_rebuild(self):
        values = {"operation": str(uuid4()), "activity": "writing",
                  "enabled": "1"}
        self.assertEqual(self.client.post("/action/pin/", values).status_code,
                         302)
        before = services.journal_path().read_bytes()
        self.client.post("/action/pin/", values)
        self.assertEqual(services.journal_path().read_bytes(), before)
        GameState.objects.all().delete()
        response = self.client.get("/activities/")
        self.assertEqual([a["key"] for a in response.context["pinned"]],
                         ["writing"])
        self.assertEqual(response.content.decode().count(
            'data-search="работа над текстом"'), 2)
        self.client.post("/action/pin/", {
            **values, "operation": str(uuid4()), "enabled": "",
        })
        self.assertEqual(services.pinned_activities(services.refresh()[0]), [])

    def test_developer_details_are_server_rendered_only_when_enabled(self):
        response, data = self.preview_data()
        for text in ("Выполнение №", "Предварительный расчёт",
                     "в долг", "остаток"):
            self.assertNotContains(response, text)
        ordinary = response.context["preview_rows"]
        enable_developer(self.client)
        response = self.client.post("/preview/", data)
        self.assertEqual(response.context["preview_rows"], ordinary)
        for text in ("Выполнение №", "Предварительный расчёт",
                     "в долг", "остаток"):
            self.assertContains(response, text)
        data = response.context["activity_form"].data.dict()
        result = self.client.post("/action/complete/", data)
        self.assertContains(self.client.get(result.url), "Выполнение №")
        self.client.post("/action/developer/", {"enabled": "0"})
        normal = self.client.get(result.url)
        for text in ("Выполнение №", "в долг", "остаток"):
            self.assertNotContains(normal, text)
        self.assertContains(normal, "+62 XP")

    def test_auto_preview_keeps_day_after_boundary_change(self):
        moment = datetime(2026, 11, 15, 4, 30,
                          tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            _, data = self.preview_data()
            services.execute("settings", {
                "timezone": "Europe/Moscow", "boundary_minutes": 300,
                "backup_on_change": False,
            }, str(uuid4()))
            result = self.client.post("/action/complete/", data)
            self.assertEqual(result.status_code, 302)
            event = read_events(services.journal_path())[-1]
            self.assertEqual(event["game_date"], "2026-11-15")
            response, _ = self.preview_data()
            self.assertEqual(str(response.context["selected_day"]),
                             "2026-11-14")

    def test_weekly_quests_out_of_order_and_old_snapshot(self):
        moment = datetime(2026, 11, 15, 12,
                          tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            complete(services.journal_path(), catalog()["eagle"], "2",
                     str(uuid4()), moment,
                     credited_day=moment.date() + timedelta(days=1))
            for _ in range(2):
                _, data = self.preview_data("previous", "eagle")
                self.assertEqual(self.client.post("/action/complete/", data)
                                 .status_code, 302)
            events, state = services.refresh()
            periods = state["quests"]["completed_periods"]["eagle"]
            self.assertEqual(periods, ["2026-11-09", "2026-11-16"])
            from liferpg.domain.quests import quest_state
            from copy import deepcopy
            old = deepcopy(events)
            for event in old:
                event["payload"].get("quest_state", {}).pop(
                    "completed_periods", None
                )
            self.assertEqual(quest_state(old)["completed_periods"]["eagle"],
                             periods)


class ContentEditorTests(TestCase):
    setUp = WebTests.setUp

    def post(self, command, **fields):
        return self.client.post(f"/action/{command}/", {
            "operation": str(uuid4()), **fields,
        })

    def enable(self):
        enable_developer(self.client)

    def create(self, kind, **fields):
        if kind in ("quest", "achievement"):
            text = f'Выполнить: {fields.get("threshold", 1)} выполнений'
            fields.setdefault("condition_text", text)
        response = self.post(f"create-{kind}", **fields)
        self.assertEqual(response.status_code, 302, response.content[:200])
        return read_events(services.journal_path())[-1]["entity_id"]

    def complete(self, activity, status=302):
        response = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": activity,
            "intensity": "2", "day_choice": "current",
        })
        if status == 400:
            self.assertEqual(response.status_code, 400)
            return
        self.assertEqual(response.status_code, 200)
        data = response.context["activity_form"].data.dict()
        self.assertEqual(self.client.post("/action/complete/", data)
                         .status_code, status)

    def test_guard_and_perk_redirect(self):
        for command in ("create-activity", "create-achievement",
                        "create-quest", "perk"):
            self.assertEqual(self.post(command).status_code,
                             403 if command == "perk" else 400)
        self.assertEqual(self.client.get("/developer/").status_code, 404)
        stats = self.client.get("/stats/")
        self.assertContains(stats, "Ленивый")
        self.assertNotContains(stats, 'action="/action/perk/"')
        self.assertRedirects(self.client.get("/perks/"), "/stats/#perks")
        self.enable()
        self.assertContains(self.client.get("/stats/"),
                            'action="/action/perk/"')
        self.assertNotContains(self.client.get("/developer/"),
                               "Состояние перков")
        self.assertEqual(self.post("perk", perk="lazy").status_code, 302)
        self.assertNotIn("lazy", services.refresh()[1]["perks"])

    def test_locked_activity_rewards_achievements_and_rebuild(self):
        self.enable()
        activity = self.create(
            "activity", name="Тестовая прогулка", category="Улица",
            description="Прогулка в парке", condition_type="stat",
            stat="strength", threshold=2, xp_will=10,
        )
        self.assertContains(self.client.get("/activities/"),
                            "Сила: требуется уровень 2")
        self.complete(activity, status=400)
        self.assertEqual(self.post("pin", activity=activity, enabled="1")
                         .status_code, 302)
        award = self.create(
            "achievement", name="Две прогулки", description="Описание",
            condition_type="activity", activity=activity, threshold=2,
        )
        page = self.client.get("/achievements/")
        self.assertContains(page, "2 выполнений")
        self.assertContains(page, "Описание")
        self.post("level", key="strength", value=2)
        self.complete(activity)
        self.assertNotIn(award, services.refresh()[1]["achievements"])
        self.complete(activity)
        self.complete(activity)
        recent = self.create(
            "achievement", name="Сильный", condition_type="stat",
            stat="strength", threshold=2,
        )
        events, state = services.refresh()
        awards = [effect["achievement"] for event in events
                  for effect in event["payload"].get("consequences", ())
                  if effect["type"] == "achievement.unlocked"]
        self.assertEqual(awards.count(award), 1)
        self.assertEqual(state["stats"]["will"]["xp"], 31)
        self.assertIn(recent, awards)
        GameState.objects.all().delete()
        RecordedEvent.objects.all().delete()
        self.assertEqual(services.refresh()[1], state)
        page = self.client.get("/achievements/")
        self.assertEqual([a["key"] for a in page.context["achievements"][:2]],
                         [award, recent])
        self.assertEqual(self.client.get("/activities/").context["pinned"][0]
                         ["key"], activity)

    def test_activity_gate_is_rechecked_on_confirmation(self):
        self.enable()
        key = self.create(
            "activity", name="Запертая активность", category="Тело",
            condition_type="stat", stat="strength", threshold=2, xp_will=10,
        )
        self.post("level", key="strength", value=2)
        preview = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": key,
            "intensity": "2", "day_choice": "current",
        })
        self.assertEqual(preview.status_code, 200)
        data = preview.context["activity_form"].data.dict()
        self.post("level", key="strength", value=1)
        before = services.journal_path().read_bytes()
        self.assertEqual(self.client.post("/action/complete/", data)
                         .status_code, 400)
        self.assertEqual(services.journal_path().read_bytes(), before)

    def test_quest_progress_creation_order_and_period_rollover(self):
        self.enable()
        moment = datetime(2026, 11, 16, 12,
                          tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("webgame.services.local_now", return_value=moment):
            self.complete("writing")
            daily = self.create("quest", name="За день", period="daily",
                                activity="writing", count=2)
            weekly = self.create("quest", name="За неделю", period="weekly",
                                 activity="writing", count=2)
            retro = self.create("quest", name="Сразу", period="daily",
                                activity="writing", count=1)
            streak = self.create(
                "achievement", name="Дневная серия", condition_type="streak",
                streak=daily, threshold=1,
            )
            page = self.client.get("/quests/")
            self.assertContains(page, "Работа над текстом: 1 из 2", count=2)
            self.complete("writing")
            page = self.client.get("/quests/")
            self.assertEqual([q["key"] for q in page.context["daily"][:3]],
                             ["writing", retro, daily])
            self.assertEqual(page.context["weekly"][0]["key"], weekly)
            before = services.refresh()[1]
            GameState.objects.all().delete()
            self.assertEqual(services.refresh()[1], before)
            self.assertIn(streak, before["achievements"])
        with patch("webgame.services.local_now",
                   return_value=moment + timedelta(days=1)):
            page = self.client.get("/quests/")
            self.assertFalse(any(q["done"] for q in page.context["daily"]))
            self.assertEqual(page.context["daily"][0]["key"], "sleep")
            self.assertTrue(page.context["weekly"][0]["done"])
        with patch("webgame.services.local_now",
                   return_value=moment + timedelta(days=7)):
            page = self.client.get("/quests/")
            self.assertFalse(any(q["done"] for q in page.context["weekly"]))

    def test_invalid_content_and_builtin_achievement_order(self):
        self.enable()
        self.assertEqual(self.post("create-quest", name="Ошибка",
                                   period="daily", activity="unknown", count=2)
                         .status_code,
                         400)
        self.assertEqual(self.post("create-achievement", name="Ошибка",
                                   condition_type="stat", threshold=0)
                         .status_code, 400)
        self.assertEqual(len(read_events(services.journal_path())), 1)
        self.post("achievement", key="skill-five")
        self.post("achievement", key="first-level")
        page = self.client.get("/achievements/")
        self.assertEqual([a["key"] for a in page.context["achievements"][:3]],
                         ["skill-five", "first-level", "all-five"])
        self.assertTrue(all(a["condition"]
                            for a in page.context["achievements"]))

    def test_display_hides_developer_for_old_sessions(self):
        self.enable()
        third_party_tab = Client()
        third_party_tab.cookies = self.client.cookies.copy()
        self.assertEqual(third_party_tab.get("/developer/").status_code, 200)
        set_display(self.client)
        set_display(Client(), show_developer="on")
        self.assertEqual(third_party_tab.post("/action/create-skill/", {})
                         .status_code, 400)
        self.assertEqual(third_party_tab.post("/action/level/", {})
                         .status_code, 403)
        self.assertNotContains(third_party_tab.get("/developer/"),
                               "Создать базовый навык")
        self.assertContains(self.client.get("/stats/"), "Модификаторы")

    def test_overview_panels_and_manual_selection(self):
        self.enable()
        self.assertNotContains(self.client.get("/"), "Следующий шаг")
        daily = self.create("quest", name="Новый дейлик", period="daily",
                            activity="walk", count=1)
        weekly = self.create("quest", name="Новый виклик", period="weekly",
                             activity="walk", count=1)
        home = self.client.get("/")
        self.assertContains(home, "Новый дейлик")
        self.assertContains(home, "Новый виклик")
        set_display(self.client, daily_mode="manual", weekly_mode="manual",
                    daily_pins=[daily], hide_done="on", show_next="on")
        home = self.client.get("/")
        self.assertContains(home, "Следующий шаг")
        self.assertContains(home, "Нет закреплённых заданий.")
        self.assertNotContains(home, "Новый виклик")
        self.assertEqual(self.post("quick-activity", activity="walk")
                         .status_code, 204)
        self.complete("walk")
        home = self.client.get("/")
        self.assertContains(home, "Закреплённые задания выполнены!")
        self.assertEqual(home.context["activity_form"].initial["activity"],
                         "walk")
        set_display(self.client, weekly_mode="manual", weekly_pins=[weekly])
        self.assertContains(self.client.get("/"), "Новый виклик")

    def test_description_edit_and_skill_replay(self):
        self.enable()
        for kind, target, path in (
            ("quest", "writing", "/quests/"),
            ("achievement", "first-level", "/achievements/"),
            ("perk", "lazy", "/stats/"), ("modifier", "rust", "/stats/"),
        ):
            values = {"kind": kind, "target": target,
                      "condition_text": "Локальное описание"}
            if kind == "achievement":
                values["description"] = "Текст достижения"
            self.assertEqual(self.post("description", **values).status_code,
                             302)
            self.assertContains(self.client.get(path), "Локальное описание")
            self.assertEqual(self.post("description", **values,
                                       name="Подмена").status_code, 400)
        key = self.create("skill", name="Выход на улицу", description="Навык",
                          links='{"walk":1100,"run":150}')
        self.complete("walk")
        self.complete("run")
        _, state = services.refresh()
        self.assertEqual(state["root_skills"][key],
                         {"level": 2, "xp": 250, "mastered": False})
        GameState.objects.all().delete()
        self.assertEqual(services.refresh()[1], state)
        self.assertContains(self.client.get("/skills/"), "Выход на улицу")

    def test_disable_autostart_clears_quiet(self):
        with patch.object(
            settings, "LIFERPG_APPLY_SYSTEM", create=True
        ) as apply:
            self.assertEqual(self.post("system", tray="on", quiet="on")
                             .status_code, 302)
        self.assertEqual(apply.call_args.args[0], {
            "tray": True, "autostart": False, "quiet": False,
        })

    def test_display_page_preserves_pin_lists_in_all_mode(self):
        link = self.client.get("/settings/")
        self.assertContains(link, 'href="/display/"')
        self.assertNotContains(link, 'name="daily_pins"')
        data = {"daily_mode": "manual", "weekly_mode": "manual",
                "daily_pins": ["writing"], "weekly_pins": ["eagle"]}
        self.assertRedirects(set_display(self.client, **data), "/display/")
        overview = self.client.get("/")
        self.assertEqual(
            [q["key"] for q in overview.context["daily_overview"]["rows"]],
            ["writing"],
        )
        self.assertEqual(
            [q["key"] for q in overview.context["weekly_overview"]["rows"]],
            ["eagle"],
        )
        set_display(self.client)
        GameState.objects.all().delete()
        form = Client().get("/display/").context["display_form"]
        self.assertEqual(form["daily_mode"].value(), "all")
        self.assertEqual(form["daily_pins"].value(), ["writing"])
        self.assertEqual(form["weekly_pins"].value(), ["eagle"])

    def test_editor_routes_and_skill_validation(self):
        routes = ("developer-add", "developer-edit", "create-skill",
                  "create-activity", "create-quest", "create-achievement")
        for route in routes:
            self.assertEqual(self.client.get(f"/{route}/").status_code, 200)
        for action in (*routes[2:], "description"):
            self.assertEqual(self.post(action).status_code, 400)
        self.enable()
        for route in routes:
            self.assertEqual(self.client.get(f"/{route}/").status_code, 200)
        before = read_events(services.journal_path())
        response = self.post("create-skill", name="", description="Черновик",
                             links='{"walk":50,"run":100}')
        self.assertEqual(response.status_code, 400)
        form = response.context["content_form"]
        self.assertEqual(form["links"].value(), '{"walk":50,"run":100}')
        self.assertEqual(form["description"].value(), "Черновик")
        for invalid in ('{"alien":1}', '{"walk":false}', '{"walk":0}', '[]'):
            self.assertEqual(self.post("create-skill", name="Навык",
                                       links=invalid).status_code, 400)
        self.assertEqual(read_events(services.journal_path()), before)


class NotificationAndFreezeTests(TestCase):
    setUp = WebTests.setUp
    post = ContentEditorTests.post
    enable = ContentEditorTests.enable
    complete = ContentEditorTests.complete

    def test_notifications_persist_once_without_repeat_toasts(self):
        self.enable()
        self.complete("meditation")
        self.complete("meditation")
        page = self.client.get("/notifications/")
        rows = page.context["notification_recent"]
        self.assertTrue(any("уровень" in row["text"] for row in rows))
        self.assertTrue(any("выполнено" in row["text"] for row in rows))
        self.assertTrue(any("Достижение" in row["text"] for row in rows))
        self.assertEqual(len(rows), len({row["id"] for row in rows}))
        self.assertFalse(
            self.client.get("/").context["notification_toasts"]
        )
        self.post("level", key="player", value=8)
        page = self.client.get("/")
        self.assertEqual(page.context["notification_toasts"][0]["text"],
                         "Общий уровень: 8!")
        GameState.objects.all().delete()
        page = Client().get("/notifications/")
        self.assertTrue(any("Общий уровень: 8!" == r["text"]
                            for r in page.context["notification_recent"]))
        self.assertEqual(page.context["notification_toasts"], [])

    def test_split_display_preserves_other_settings(self):
        set_display(self.client, daily_mode="manual", daily_pins=["writing"],
                    show_next="on", show_developer="on")
        response = self.post("display", panel="quests", daily_mode="manual",
                             weekly_mode="all", daily_pins=["meditation"])
        self.assertEqual(response.status_code, 302)
        from liferpg.preferences import preferences
        display = preferences(services.refresh()[0], "display.updated")
        self.assertTrue(display["show_next"])
        self.assertTrue(display["show_developer"])
        self.post("display", panel="elements", show_perks="on")
        display = preferences(services.refresh()[0], "display.updated")
        self.assertEqual(display["daily_pins"], ["meditation"])
        self.assertTrue(display["show_perks"])

    def test_freeze_settings_and_calendar_replay(self):
        from liferpg.domain.quests import advance, quest_state
        from datetime import date
        self.assertEqual(self.post("freezes").status_code, 403)
        self.enable()
        with patch("webgame.services.local_now", return_value=datetime(
            2026, 11, 16, 12, tzinfo=ZoneInfo("Europe/Moscow")
        )):
            response = self.post("freezes", kind="daily", stock=2, maximum=7,
                                 amount=3, interval=2)
        self.assertEqual(response.status_code, 302)
        events, saved = services.refresh()
        state = quest_state(events)
        advance(state, date(2026, 11, 23))
        self.assertEqual(state["banks"]["daily"], 2)
        advance(state, date(2026, 11, 30))
        self.assertEqual(state["banks"]["daily"], 5)
        advance(state, date(2026, 11, 30))
        self.assertEqual(state["banks"]["daily"], 5)
        self.assertEqual(self.post("freezes", kind="daily", stock=2,
                                   maximum=1, amount=1, interval=1)
                         .status_code, 400)
        self.assertEqual(self.post("freezes", kind="daily", stock=0,
                                   maximum=1, amount=1, interval=0)
                         .status_code, 400)
        GameState.objects.all().delete()
        self.assertEqual(services.refresh()[1], saved)

    def test_atomic_startup_backup_keeps_success_on_failure(self):
        from liferpg.automatic_backup import startup_backup
        self.enable()
        self.complete("writing")
        path = services.journal_path()
        result = startup_backup(path)
        original = result.read_bytes()
        self.assertEqual(original, path.read_bytes())
        self.complete("writing")
        with patch("liferpg.automatic_backup.os.replace",
                   side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                startup_backup(path)
        self.assertEqual(result.read_bytes(), original)
        startup_backup(path)
        self.assertEqual(result.read_bytes(), path.read_bytes())
        with path.open("ab") as target:
            target.write(b'{"broken":')
        with self.assertRaises(ValueError):
            startup_backup(path)
        self.assertTrue(result.read_bytes().endswith(b"\n"))

    def test_weekly_bank_monthly_interval(self):
        from liferpg.domain.quests import advance, quest_state
        from datetime import date
        self.enable()
        with patch("webgame.services.local_now", return_value=datetime(
            2026, 11, 16, 12, tzinfo=ZoneInfo("Europe/Moscow")
        )):
            response = self.post("freezes", kind="weekly", stock=0,
                                 maximum=4, amount=2, interval=2)
        self.assertEqual(response.status_code, 302)
        state = quest_state(services.refresh()[0])
        advance(state, date(2026, 12, 1))
        self.assertEqual(state["banks"]["weekly"], 0)
        advance(state, date(2027, 1, 1))
        self.assertEqual(state["banks"]["weekly"], 2)
        advance(state, date(2027, 1, 1))
        self.assertEqual(state["banks"]["weekly"], 2)

    def test_perk_slot_replacement_and_backup_restore(self):
        from liferpg.automatic_backup import startup_backup
        self.enable()
        before = self.client.get("/stats/").context["perks"]
        self.assertEqual(before[0]["key"], "lazy")
        self.assertNotIn("victory", [p["key"] for p in before])
        self.post("level", key="will", value=5)
        after = self.client.get("/stats/").context["perks"]
        self.assertEqual(after[0]["key"], "victory")
        self.assertNotIn("lazy", [p["key"] for p in after])
        events, state = services.refresh()
        backup = startup_backup(services.journal_path())
        restored = read_events(backup)
        self.assertEqual(restored, events)
        self.assertEqual(services.snapshot(restored), state)

    def test_notification_text_and_local_timestamp_format(self):
        from webgame.notifications import history
        event = {
            "event_id": "format-test",
            "event_type": "activity.completed",
            "local_datetime": "2026-11-19T03:27:49+03:00",
            "payload": {"consequences": [
                {"type": "stat.leveled_up", "stat": "will", "level": 2},
            ]},
        }
        rows = history([event])
        self.assertEqual(rows[0]["text"], "Воля: уровень 2!")
        self.assertEqual(rows[0]["time"], "3:27, 19.11.26")


class EditableRuleTextTests(TestCase):
    setUp = WebTests.setUp
    post = ContentEditorTests.post
    enable = ContentEditorTests.enable
    create = ContentEditorTests.create
    complete = ContentEditorTests.complete

    def test_texts_are_required_and_cannot_change_parameters(self):
        self.assertEqual(self.post("description", kind="perk", target="lazy",
                                   condition_text="Текст").status_code, 302)
        self.enable()
        for text in ("", "   "):
            self.assertEqual(self.post("description", kind="quest",
                                       target="writing", condition_text=text)
                             .status_code, 400)
        self.assertEqual(self.post("description", kind="perk", target="lazy",
                                   condition_text="Текст", factor="100")
                         .status_code, 400)
        self.assertEqual(self.post("create-quest", name="Без текста",
                                   period="daily", activity="writing", count=1)
                         .status_code, 400)
        self.assertEqual(self.post("create-achievement", name="Без текста",
                                   condition_type="stat", stat="will",
                                   threshold=2).status_code, 400)
        form = self.client.get("/create-activity/").context["content_form"]
        self.assertNotIn("description", form.fields)

    def test_custom_texts_replay_without_changing_real_conditions(self):
        self.enable()
        quest = self.create("quest", name="Две записи", period="daily",
                            activity="writing", count=2,
                            condition_text="Начальный текст")
        award = self.create("achievement", name="Уровень Воли",
                            condition_type="stat", stat="will", threshold=2,
                            description="Описание награды",
                            condition_text="Условие награды")
        for kind, target in (("quest", quest), ("quest", "writing"),
                             ("achievement", award), ("perk", "llm"),
                             ("modifier", "rust")):
            values = {"kind": kind, "target": target,
                      "condition_text": "Пользовательский текст " + target}
            if kind == "achievement":
                values["description"] = "Новое описание"
            self.assertEqual(self.post("description", **values).status_code,
                             302)
        self.complete("writing")
        state = services.refresh()[1]
        self.assertNotIn(quest, state["quests"]["completed"])
        self.assertNotIn(award, state["achievements"])
        self.complete("writing")
        self.assertIn(quest, services.refresh()[1]["quests"]["completed"])
        self.complete("meditation")
        self.complete("meditation")
        self.assertIn(award, services.refresh()[1]["achievements"])
        before = services.refresh()[1]
        GameState.objects.all().delete()
        self.assertEqual(services.refresh()[1], before)
        self.assertContains(self.client.get("/quests/"),
                            "Пользовательский текст " + quest)
        self.assertContains(
            self.client.get("/achievements/"), "Новое описание"
        )
        self.assertContains(self.client.get("/stats/"),
                            "Пользовательский текст llm")
        self.assertContains(self.client.get("/stats/"),
                            "Пользовательский текст rust")
        self.assertIn("llm", before["perks"])


class AdditionalAchievementConditionTests(TestCase):
    setUp = WebTests.setUp
    post = ContentEditorTests.post
    enable = ContentEditorTests.enable
    create = ContentEditorTests.create
    complete = ContentEditorTests.complete

    def test_any_stat_specific_stat_and_immediate_unlock(self):
        self.enable()
        any_stat = self.create("achievement", name="Любая до двух",
                               condition_type="stat", stat="any", threshold=2)
        specific = self.create("achievement", name="Интеллект до двух",
                               condition_type="stat", stat="intelligence",
                               threshold=2)
        self.assertNotIn(any_stat, services.refresh()[1]["achievements"])
        self.post("level", key="agility", value=2)
        state = services.refresh()[1]
        self.assertIn(any_stat, state["achievements"])
        self.assertNotIn(specific, state["achievements"])
        self.post("level", key="intelligence", value=2)
        self.assertIn(specific, services.refresh()[1]["achievements"])
        immediate = self.create("achievement", name="Уже достигнуто",
                                condition_type="stat", stat="any", threshold=2)
        event = read_events(services.journal_path())[-1]
        self.assertEqual(event["event_type"], "achievement.created")
        self.assertEqual(event["payload"]["xp_by_stat"], {})
        self.assertIn(immediate, services.refresh()[1]["achievements"])
        from webgame.notifications import history
        self.assertTrue(any(row["event"] == event["event_id"]
                            for row in history(read_events(
                                services.journal_path()
                            ))))

    def test_skill_threshold_and_repeated_processing(self):
        self.enable()
        skill = self.create("skill", name="Практика", links='{"walk":500}')
        specific = self.create("achievement", name="Практик",
                               condition_type="skill", skill=skill,
                               threshold=2)
        any_skill = self.create("achievement", name="Любой навык",
                                condition_type="skill", skill="any",
                                threshold=2)
        self.complete("walk")
        self.assertNotIn(specific, services.refresh()[1]["achievements"])
        preview = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": "walk",
            "intensity": "2", "day_choice": "current",
        })
        values = preview.context["activity_form"].data.dict()
        self.assertEqual(self.client.post("/action/complete/", values)
                         .status_code, 302)
        before = services.journal_path().read_bytes()
        self.client.post("/action/complete/", values)
        self.assertEqual(services.journal_path().read_bytes(), before)
        immediate = self.create("achievement", name="Уже развит",
                                condition_type="skill", skill=skill,
                                threshold=2)
        state = services.refresh()[1]
        for key in (specific, any_skill, immediate):
            self.assertIn(key, state["achievements"])
        events = read_events(services.journal_path())
        effects = [effect["achievement"] for event in events
                   for effect in event["payload"].get("consequences", ())
                   if effect["type"] == "achievement.unlocked"]
        self.assertEqual(effects.count(specific), 1)
        self.assertEqual(effects.count(any_skill), 1)
        GameState.objects.all().delete()
        RecordedEvent.objects.all().delete()
        self.assertEqual(services.refresh()[1], state)

    def test_writing_root_and_validation_of_selection(self):
        self.enable()
        form = self.client.get("/create-achievement/").context["content_form"]
        self.assertIn(("any", "Любая"), form.fields["stat"].choices)
        self.assertIn(("writing", "Писательство"),
                      form.fields["skill"].choices)
        key = self.create("achievement", name="Писатель",
                          condition_type="skill",
                          skill="writing", threshold=1)
        self.assertIn(key, services.refresh()[1]["achievements"])
        for fields in (
            {"skill": "unknown", "threshold": 2},
            {"skill": "writing", "threshold": 16},
            {"skill": "any", "threshold": 0},
        ):
            response = self.post("create-achievement", name="Ошибка",
                                 condition_type="skill",
                                 condition_text="Текст",
                                 **fields)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post("create-activity", name="Ошибка",
                                   category="Тест", xp_will=1,
                                   condition_type="stat", stat="any",
                                   threshold=2).status_code, 400)

    def test_archived_skill_excluded_from_creation_and_awards(self):
        self.enable()
        from liferpg.game import make_event
        from liferpg.journal import append_event
        from liferpg.domain.achievements import evaluate_custom
        from liferpg.domain.character import Character
        moment = datetime(2026, 11, 20, 12,
                          tzinfo=ZoneInfo("Europe/Moscow"))
        events = read_events(services.journal_path())
        archived = make_event(
            events, str(uuid4()), "skill.created", "archived-root", moment,
            {"xp_by_stat": {}, "definition": {
                "name": "Архив", "description": "", "links": {"walk": 1000},
                "archived": True,
            }, "root_skill_progress": {
                "archived-root": {"level": 15, "xp": 0, "mastered": True},
            }}, "Архивный навык",
        )
        append_event(services.journal_path(), archived)
        form = self.client.get("/create-achievement/").context["content_form"]
        self.assertNotIn("archived-root", dict(form.fields["skill"].choices))
        self.assertEqual(self.post(
            "create-achievement", name="Ошибка", condition_type="skill",
            skill="archived-root", threshold=2, condition_text="Текст",
        ).status_code, 400)
        key = self.create("achievement", name="Только активные",
                          condition_type="skill", skill="any",
                          threshold=2)
        self.assertNotIn(key, services.refresh()[1]["achievements"])
        events = read_events(services.journal_path())
        payload = {"xp_by_stat": {}, "consequences": [],
                   "root_skill_progress": {
                       "archived-root": {
                           "level": 15, "xp": 100, "mastered": True,
                       },
                   }}
        evaluate_custom(events, Character(), payload)
        self.assertEqual(payload["consequences"], [])


from webgame.catalog_tests import (  # noqa: E402,F401
    CatalogTests, OnboardingTests,
)
