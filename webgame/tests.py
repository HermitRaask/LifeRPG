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


class WebTests(TestCase):
    def setUp(self):
        folder = settings.BASE_DIR / ".venv"
        folder.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(prefix="web-test-", dir=folder)
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(DATA_DIR=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.client = Client()

    def perform(self, activity="meditation", operation=None):
        return services.execute("complete", {
            "activity": activity, "intensity": "2"
        }, operation or str(uuid4()))

    def test_new_save_and_all_pages(self):
        for page in ("", "activities/", "stats/", "skills/", "quests/",
                     "perks/", "achievements/", "history/", "reports/",
                     "settings/", "developer/"):
            response = self.client.get("/" + page)
            self.assertEqual(response.status_code, 200, page)
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
        self.assertContains(response, "+75 XP")
        self.assertEqual(read_events(services.journal_path()), [])
        response = self.client.post("/action/complete/", data)
        self.assertEqual(response.status_code, 302)
        self.assertContains(
            self.client.get(response.url), "Действие сохранено"
        )
        original = GameState.objects.get().data
        self.client.post("/action/complete/", data)
        self.assertEqual(GameState.objects.get().data, original)
        self.client.post("/action/complete/",
                         {**data, "operation": str(uuid4())})
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
        self.assertEqual(state["stats"]["will"]["xp"], 75)
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
        self.client.post("/action/developer/", {"enabled": "1"})
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
                         150)
        self.assertGreater(new["rules_version"], original["rules_version"])
        self.assertEqual(read_events(services.journal_path())[1], original)
        with self.assertRaises(ValueError):
            self.perform("writing", original["event_id"])

    def test_time_boundary_and_settings_lock(self):
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
        with self.assertRaises(ValueError):
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
        self.assertEqual(monthly.data["xp"]["will"], 75)
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
        self.client.post("/action/developer/", {"enabled": "1"})
        for factors in ("1 1", "1 0 -1", "1 nan 1", "1 1/0 1"):
            response = self.client.post("/action/rules/", {
                "operation": str(uuid4()), "intensity": factors,
                "repetition": "1 1 .6 .3 .15",
            })
            self.assertEqual(response.status_code, 400)
        self.assertEqual(read_events(services.journal_path()), [])

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
        self.assertEqual(new["rules_version"], 5)
        self.assertEqual(read_events(services.journal_path())[1], events[1])

    def test_edit_decreases_level_and_duplicate_does_not_reapply(self):
        first = self.perform()
        self.perform()
        values = {"target": first["seq"], "activity": "meditation",
                  "intensity": "2", "cancel": True}
        operation = str(uuid4())
        services.execute("revise", values, operation)
        response = self.client.get(f"/result/{operation}/")
        self.assertContains(response, "-75 XP")
        state = services.refresh()[1]
        self.assertEqual(state["stats"]["will"]["level"], 1)
        self.assertEqual(state["stats"]["will"]["xp"], 75)
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
        self.assertEqual(report["xp"]["will"], 75)
        self.assertEqual(len(report["history"]), 1)
        self.assertEqual(report["achievements"], [])
        row = first["payload"]["xp_by_stat"]["will"]
        self.assertEqual(row["xp_after"], 75)

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
