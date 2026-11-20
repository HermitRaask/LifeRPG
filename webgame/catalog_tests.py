"""Onboarding and catalog lifecycle integration checks."""
from pathlib import Path
from uuid import uuid4

from django.test import TestCase, override_settings

from liferpg.domain.activities import catalog
from liferpg.domain.achievements import achievement_names
from liferpg.domain.catalog_state import (
    EMPTY_ACHIEVEMENTS, archive_state, configured, tutorial_state,
)
from liferpg.domain.skills import active_root_skills, root_progress
from liferpg.journal import read_events
from liferpg.preferences import preferences
from webgame import services
from webgame.models import GameState, RecordedEvent
from webgame import tests as base_tests


class CatalogTests(TestCase):
    setUp = base_tests.WebTests.setUp
    post = base_tests.ContentEditorTests.post
    create = base_tests.ContentEditorTests.create

    def complete(self, activity):
        response = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": activity, "intensity": "2",
            "day_choice": "current",
        })
        form = response.context["activity_form"]
        self.assertEqual(response.status_code, 200, str(form.errors))
        response = self.client.post("/action/complete/", form.data.dict())
        self.assertEqual(response.status_code, 302)

    def command(self, command, **values):
        return services.execute(command, values, str(uuid4()),
                                developer=command == "restore")

    def test_empty_profile_and_backup_rebuild(self):
        self.command("profile", preset="empty")
        events, state = services.refresh()
        self.assertFalse(catalog(events))
        self.assertFalse(active_root_skills(events))
        self.assertFalse(state["perks"])
        self.assertEqual(set(achievement_names(events)), EMPTY_ACHIEVEMENTS)
        self.assertTrue(all(r["level"] == 1 and r["xp"] == 0
                            for r in state["stats"].values()))
        activity = self.create("activity", name="Свой шаг", category="Свои",
                               condition_type="none", xp_will=100)
        self.complete(activity)
        before = services.refresh()[1]
        backup = services.backup()
        self.assertEqual(Path(backup).read_bytes(),
                         services.journal_path().read_bytes())
        GameState.objects.all().delete()
        RecordedEvent.objects.all().delete()
        restored_events, after = services.refresh()
        self.assertEqual(before, after)
        self.assertEqual(set(catalog(restored_events)), {activity})
        with self.assertRaises(ValueError):
            self.command("profile", preset="preset")

    def test_normal_editor_and_sticky_selection(self):
        activity = self.create("activity", name="Своя", category="Тест",
                               condition_type="none", xp_will=1)
        task = self.create("quest", name="Повторять", period="daily",
                           activity=activity, count=2)
        response = self.post("description", kind="quest", target=task,
                             condition_text="Сохранённый текст")
        self.assertEqual(response.status_code, 302)
        self.assertIn("kind=quest", response.url)
        response = self.client.get(response.url)
        form = response.context["description_form"]
        self.assertEqual(form.initial["target"], task)
        self.assertContains(response, "Сохранённый текст")
        self.assertEqual(self.post("level", key="will", value=3)
                         .status_code, 403)
        self.assertEqual(self.client.get("/archive/").status_code, 403)

    def test_cascade_stale_form_and_selective_restore(self):
        skill = self.create("skill", name="Прогулки", links='{"walk":1000}')
        task = self.create("quest", name="Своё задание", period="daily",
                           activity="walk", count=1)
        achievement = self.create(
            "achievement", name="Дважды", condition_type="activity",
            activity="walk", threshold=2,
        )
        self.complete("walk")
        self.assertEqual(root_progress(read_events(
            services.journal_path()))[skill]["level"], 2)
        quote = self.client.post("/preview/", {
            "operation": str(uuid4()), "activity": "walk", "intensity": "2",
            "day_choice": "current",
        }).context["activity_form"].data.dict()
        preview = self.client.get("/catalog/?kind=activity&key=walk")
        self.assertContains(preview, "Своё задание")
        before = services.journal_path().read_bytes()
        self.client.get("/catalog/")  # cancel is navigation only
        self.assertEqual(services.journal_path().read_bytes(), before)
        operation = str(uuid4())
        event = services.execute(
            "archive", {"kind": "activity", "key": "walk"}, operation
        )
        services.execute("archive", {"kind": "activity", "key": "walk"},
                         operation)
        self.assertEqual(read_events(services.journal_path())[-1], event)
        self.assertNotIn("walk", catalog(read_events(services.journal_path())))
        self.assertEqual(self.client.post("/action/complete/", quote)
                         .status_code, 400)
        self.assertContains(self.client.get("/achievements/"),
                            "Условие недоступно")
        self.assertNotIn(achievement, services.refresh()[1]["achievements"])
        with self.assertRaises(ValueError):
            services.execute("restore", {"targets": ["activity:walk"]},
                             str(uuid4()))
        with self.assertRaises(ValueError):
            self.command("restore", targets=["quest:" + task])
        self.command("restore", targets=["activity:walk", "quest:" + task])
        state = services.refresh()[1]
        self.assertEqual(state["quests"]["streaks"][task]["duration"], 0)
        self.assertEqual(state["quests"]["streaks"][task]["best"], 1)
        # A prior completion in the same period must not finish this new run.
        self.complete("reading")
        self.assertEqual(services.refresh()[1]["quests"]["streaks"][task]
                         ["duration"], 0)
        self.complete("walk")
        state = services.refresh()[1]
        self.assertEqual(state["quests"]["streaks"][task]["duration"], 1)
        self.assertIn(achievement, state["achievements"])
        # Unselected cascaded quests remain archived.
        archived = archive_state(read_events(services.journal_path()))
        self.assertTrue(any(k[0] == "quest" and r["archived"]
                            for k, r in archived.items()))
        self.assertEqual(state, services.snapshot(read_events(
            services.journal_path())))

    def test_archived_skill_no_xp_and_restore_achievement(self):
        skill = self.create("skill", name="Свой навык", links='{"walk":5000}')
        self.complete("walk")
        self.command("archive", kind="skill", key=skill)
        specific = self.create(
            "achievement", name="Любой развитый", condition_type="skill",
            skill="any", threshold=3,
        )
        before = root_progress(read_events(services.journal_path()))[skill]
        self.complete("walk")
        self.assertEqual(root_progress(read_events(
            services.journal_path()))[skill], before)
        self.assertNotIn(specific, services.refresh()[1]["achievements"])
        self.command("restore", targets=["skill:" + skill])
        self.assertEqual(root_progress(read_events(
            services.journal_path()))[skill], before)
        self.assertIn(specific, services.refresh()[1]["achievements"])
        self.assertEqual(self.client.get("/history/").status_code, 200)

    def test_restored_weekly_task_counts_only_new_confirmations(self):
        task = self.create("quest", name="На неделю", period="weekly",
                           activity="walk", count=2)
        self.complete("walk")
        self.command("archive", kind="quest", key=task)
        self.complete("walk")
        self.command("restore", targets=["quest:" + task])
        self.complete("walk")
        self.assertEqual(services.refresh()[1]["quests"]["streaks"][task]
                         ["duration"], 0)
        self.complete("walk")
        state = services.refresh()[1]
        self.assertEqual(state["quests"]["streaks"][task]["duration"], 1)
        backup = services.backup()
        GameState.objects.all().delete()
        RecordedEvent.objects.all().delete()
        services.journal_path().write_bytes(Path(backup).read_bytes())
        self.assertEqual(services.refresh()[1], state)

    def test_previously_archived_task_not_in_new_cascade(self):
        self.command("archive", kind="quest", key="writing")
        first = archive_state(read_events(services.journal_path()))[
            ("quest", "writing")]
        self.command("archive", kind="activity", key="writing")
        second = archive_state(read_events(services.journal_path()))[
            ("quest", "writing")]
        self.assertEqual(first["group"], second["group"])


@override_settings(ONBOARDING_ENABLED=True)
class OnboardingTests(TestCase):
    post = base_tests.ContentEditorTests.post

    def setUp(self):
        base_tests.WebTests.setUp(self)
        self.override_onboarding = override_settings(ONBOARDING_ENABLED=True)
        self.override_onboarding.enable()
        self.addCleanup(self.override_onboarding.disable)

    def move(self, move, **values):
        return self.client.post("/onboarding/control/", {
            "move": move, **values,
        })

    def test_skip_choose_empty_and_restart(self):
        self.assertRedirects(self.client.get("/"), "/onboarding/")
        self.assertEqual(self.client.post("/action/complete/", {}).status_code,
                         409)
        self.move("skip")
        self.assertContains(self.client.get("/onboarding/"), "Пустой профиль")
        self.move("choose", preset="empty")
        self.assertTrue(configured(read_events(services.journal_path())))
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/activities/").status_code, 200)
        self.assertFalse(catalog(read_events(services.journal_path())))

    def test_demo_isolates_personal_session_results(self):
        self.move("skip")
        self.move("choose", preset="preset")
        session = self.client.session
        session["batch_result"] = {
            "error": "PRIVATE_BATCH_RESULT", "done": 5, "total": 6,
        }
        session["notification_pending"] = ["personal-notification"]
        session.save()
        self.move("start")
        services.execute("tutorial", {
            "active": True, "finished": False, "step": 22,
        }, str(uuid4()))
        response = self.client.get("/tutorial/")
        self.assertNotContains(response, "PRIVATE_BATCH_RESULT")
        self.assertEqual(self.client.session["notification_pending"],
                         ["personal-notification"])

    def test_tutorial_personal_skill_definitions_and_display_preferences(self):
        self.move("skip")
        self.move("choose", preset="preset")
        create = base_tests.ContentEditorTests.create
        activity = create(
            self, "activity", name="Личный пример", category="Свои",
            xp_will=10, condition_type="none",
        )
        skill = create(
            self, "skill", name="Пользовательский навык",
            links='{"' + activity + '": 50}',
        )
        archived = create(
            self, "skill", name="Архивный навык", links='{"walk":50}',
        )
        services.execute("archive", {"kind": "skill", "key": archived},
                         str(uuid4()))
        base_tests.set_display(
            self.client, hide_done="on", daily_mode="manual",
            weekly_mode="manual", daily_pins=["writing"],
            weekly_pins=["eagle"],
        )
        before = services.refresh()[1]
        self.move("start")
        response = self.client.get("/tutorial/")
        self.assertFalse(response.context["display"]["show_next"])
        self.assertFalse(response.context["display"]["show_perks"])
        self.assertFalse(response.context["display"]["show_modifiers"])
        self.assertEqual(response.context["display"]["daily_mode"], "manual")
        self.assertEqual(response.context["display"]["daily_pins"],
                         ["writing"])
        self.assertNotContains(response, 'class="panel quick-panel"')
        self.assertNotContains(response, '<h2>Перки</h2>')
        services.execute("tutorial", {
            "active": True, "finished": False, "step": 8,
        }, str(uuid4()))
        response = self.client.get("/tutorial/")
        self.assertContains(response, "Пользовательский навык")
        self.assertNotContains(response, "Архивный навык")
        self.assertEqual(response.context["state"]["root_skills"][skill]["xp"],
                         50)
        self.assertEqual(services.refresh()[1], before)

    def test_all_tutorial_pages_repeat_and_developer_shutdown(self):
        from webgame.onboarding import STEPS
        self.move("start")
        for section, target, title, text in STEPS:
            response = self.client.get("/tutorial/")
            self.assertContains(response, title)
            self.assertContains(response, "Демонстрация")
            self.assertContains(response,
                                '<fieldset disabled class="tutorial-content">')
            self.assertEqual(self.client.post("/action/level/", {})
                             .status_code, 409)
            self.move("next")
        self.move("finish")
        self.move("choose", preset="preset")
        services.execute("complete", {"activity": "walk", "intensity": "2"},
                         str(uuid4()))
        base_tests.enable_developer(self.client)
        events, before = services.refresh()
        personal = [e for e in events
                    if e["event_type"] != "onboarding.updated"]
        self.move("start")
        self.assertEqual(self.client.get("/settings/").url, "/tutorial/")
        self.move("next")
        self.assertEqual(tutorial_state(read_events(
            services.journal_path()))["step"], 1)
        self.move("skip")
        events, after = services.refresh()
        self.assertEqual(before, after)
        current = [e for e in events
                   if e["event_type"] != "onboarding.updated"]
        self.assertEqual(personal, current)
        self.assertFalse(preferences(events, "display.updated")
                         ["show_developer"])
        self.assertNotIn("developer", self.client.session)
        self.assertEqual(self.client.get("/").status_code, 200)
