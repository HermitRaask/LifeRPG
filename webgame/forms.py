"""Structural validation only; reward calculations belong to the core."""
from fractions import Fraction
from json import loads
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms

from liferpg.domain.activities import catalog
from liferpg.domain.achievements import ACHIEVEMENTS
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.perks import PERKS
from liferpg.domain.quests import QUESTS


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field, forms.BooleanField)
                else "form-select" if isinstance(field, forms.ChoiceField)
                else "form-control"
            )


class OperationForm(StyledForm):
    operation = forms.UUIDField(widget=forms.HiddenInput)


class ActivityForm(OperationForm):
    activity = forms.ChoiceField(
        label="Активность", choices=[(k, v.name) for k, v in catalog().items()]
    )
    intensity = forms.ChoiceField(
        label="Интенсивность", initial="2",
        choices=[("1", "Лёгкая"), ("2", "Обычная"), ("3", "Высокая")],
    )


class CompletionForm(ActivityForm):
    day_choice = forms.ChoiceField(
        label="День зачёта", initial="current", required=False, choices=[
            ("current", "Текущий игровой день"),
            ("previous", "Предыдущий игровой день"),
        ],
    )
    quote = forms.CharField(required=False, widget=forms.HiddenInput)


class PinForm(OperationForm):
    activity = forms.ChoiceField(
        choices=[(k, v.name) for k, v in catalog().items()]
    )
    enabled = forms.BooleanField(required=False)


class RevisionForm(ActivityForm):
    target = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    cancel = forms.BooleanField(label="Отменить выполнение", required=False)


class BatchForm(ActivityForm):
    amount = forms.IntegerField(label="Количество выполнений", min_value=1)


class PerkForm(OperationForm):
    perk = forms.ChoiceField(label="Перк", choices=[
        (key, name) for key, name in PERKS.items() if key != "victory"
    ])
    enabled = forms.BooleanField(label="Включён", required=False)


class LevelForm(OperationForm):
    key = forms.ChoiceField(
        label="Характеристика", choices=[
            *ATTRIBUTE_NAMES.items(), ("player", "Общий уровень (вручную)")
        ]
    )
    value = forms.IntegerField(label="Новый уровень", min_value=1)


class StreakForm(OperationForm):
    key = forms.ChoiceField(label="Серия", choices=[
        (key, row[0]) for key, row in QUESTS.items()
    ])
    value = forms.IntegerField(label="Длина серии", min_value=0)


class AchievementForm(OperationForm):
    key = forms.ChoiceField(label="Достижение", choices=ACHIEVEMENTS.items())


class SettingsForm(OperationForm):
    timezone = forms.CharField(label="Часовой пояс IANA", max_length=80)
    boundary = forms.TimeField(
        label="Начало игрового дня", input_formats=["%H:%M"],
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    backup_on_change = forms.BooleanField(
        label="Резервная копия после каждого изменения", required=False
    )

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise forms.ValidationError("Неизвестный часовой пояс IANA.")
        return value


class RulesForm(OperationForm):
    intensity = forms.CharField(
        label="Коэффициенты интенсивности",
        help_text="Три числа через пробел: лёгкая, обычная, высокая.",
    )
    repetition = forms.CharField(
        label="Коэффициенты повторов",
        help_text="Пять чисел: 1-й, 2-й, 3-й, 4-й, 5-й и последующие.",
    )

    def factors(self, name, count):
        try:
            result = [Fraction(v.replace(",", "."))
                      for v in self.cleaned_data[name].split()]
            if (len(result) != count
                    or any(v <= 0 or v > 100 for v in result)):
                raise ValueError
            return [str(v) for v in result]
        except (ValueError, ZeroDivisionError):
            raise forms.ValidationError(
                f"Нужно {count} чисел в диапазоне (0; 100]."
            )

    def clean_intensity(self):
        factors = self.factors("intensity", 3)
        return dict(zip(("1", "2", "3"), factors))

    def clean_repetition(self):
        return self.factors("repetition", 5)


class PeriodForm(StyledForm):
    start = forms.DateField(
        label="С", required=False,
        widget=forms.DateInput(attrs={"type": "date"})
    )
    end = forms.DateField(
        label="По", required=False,
        widget=forms.DateInput(attrs={"type": "date"})
    )

    def clean(self):
        data = super().clean()
        if (data.get("start") and data.get("end")
                and data["start"] > data["end"]):
            raise forms.ValidationError("Начало периода позже конца.")
        return data


class ContentForm(OperationForm):
    name = forms.CharField(label="Название", max_length=100)


class DescribedContentForm(ContentForm):
    description = forms.CharField(
        label="Описание", required=False, max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    threshold = forms.IntegerField(label="Порог", required=False, min_value=1)
    stat = forms.ChoiceField(label="Характеристика", required=False,
                             choices=[("", "—"), *ATTRIBUTE_NAMES.items()])

    def clean(self):
        data = super().clean()
        mode = data.get("condition_type")
        target_field = {
            "stat": "stat", "skill": "skill",
            "activity": "activity", "streak": "streak",
        }.get(mode)
        if target_field:
            for field in (target_field, "threshold"):
                if not data.get(field) and field not in self.errors:
                    self.add_error(field, "Заполните параметр условия.")
        return data

    def definition(self):
        data = self.cleaned_data
        mode = data["condition_type"]
        condition = {"type": mode}
        if mode != "none":
            condition.update(target=data[mode], threshold=data["threshold"])
        return {"name": data["name"],
                "description": data.get("description", ""),
                "condition": condition}


class CreateActivityForm(DescribedContentForm):
    category = forms.CharField(label="Категория", max_length=80)
    condition_type = forms.ChoiceField(label="Доступность", choices=[
        ("none", "Без условия"), ("stat", "Требуется уровень характеристики"),
    ])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("description", None)
        for key, name in ATTRIBUTE_NAMES.items():
            self.fields[f"xp_{key}"] = forms.IntegerField(
                label=f"{name} · базовый XP", required=False, min_value=0,
                widget=forms.NumberInput(attrs={"class": "form-control"}),
            )
        self.order_fields(["name", "description", "category",
                           "condition_type", "stat", "threshold"])

    def clean(self):
        data = super().clean()
        if not any(data.get(f"xp_{key}") for key in ATTRIBUTE_NAMES):
            self.add_error(None, "Укажите базовый XP для характеристики.")
        return data

    def definition(self):
        data = self.cleaned_data
        return {**super().definition(), "category": data["category"],
                "rewards": {key: data[f"xp_{key}"] for key in ATTRIBUTE_NAMES
                            if data.get(f"xp_{key}")}}


class CreateAchievementForm(DescribedContentForm):
    description = forms.CharField(
        label="Описание достижения", required=False, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    condition_text = forms.CharField(
        label="Описание условия получения", max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean(self):
        data = super().clean()
        if (data.get("condition_type") == "skill"
                and data.get("threshold", 0) and data["threshold"] > 15):
            self.add_error("threshold", "Максимальный уровень навыка — 15.")
        return data

    def definition(self):
        return {**super().definition(),
                "condition_text": self.cleaned_data["condition_text"]}

    stat = forms.ChoiceField(
        label="Характеристика", required=False,
        choices=[("", "—"), ("any", "Любая"), *ATTRIBUTE_NAMES.items()],
    )
    skill = forms.ChoiceField(label="Базовый навык", required=False)
    condition_type = forms.ChoiceField(label="Условие получения", choices=[
        ("activity", "Выполнить активность N раз"),
        ("stat", "Достичь уровня N характеристики"),
        ("skill", "Уровень базового навыка"),
        ("streak", "Довести серию до N"),
    ])
    activity = forms.ChoiceField(label="Активность", required=False)
    streak = forms.ChoiceField(label="Серия", required=False)
    field_order = ["name", "description", "condition_text", "condition_type",
                   "activity", "stat", "skill", "streak", "threshold"]


class CreateQuestForm(ContentForm):
    condition_text = forms.CharField(
        label="Описание условия выполнения", max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    period = forms.ChoiceField(label="Периодичность", choices=[
        ("daily", "Ежедневное"), ("weekly", "Еженедельное"),
    ])
    activity = forms.ChoiceField(label="Активность")
    count = forms.IntegerField(label="Число выполнений", min_value=1)

    def definition(self):
        return {key: self.cleaned_data[key]
                for key in ("name", "period", "activity",
                            "count", "condition_text")}


class DisplayForm(OperationForm):
    daily_mode = forms.ChoiceField(label="Задания на сегодня")
    weekly_mode = forms.ChoiceField(label="Задания на неделю")
    daily_pins = forms.MultipleChoiceField(
        label="Закреплённые ежедневные", required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    weekly_pins = forms.MultipleChoiceField(
        label="Закреплённые еженедельные", required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    hide_done = forms.BooleanField(
        label="Скрывать выполненные задания", required=False,
    )
    show_next = forms.BooleanField(label="Следующий шаг", required=False)
    show_perks = forms.BooleanField(label="Перки", required=False)
    show_modifiers = forms.BooleanField(label="Модификаторы", required=False)
    show_developer = forms.BooleanField(
        label="Показывать режим разработчика", required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ("daily_mode", "weekly_mode"):
            self.fields[field].choices = [
                ("all", "Все задания"), ("manual", "Только закреплённые"),
            ]


class SystemForm(OperationForm):
    tray = forms.BooleanField(label="Сворачивать в трей при закрытии",
                              required=False)
    autostart = forms.BooleanField(label="Запускать при входе в Windows",
                                   required=False)
    quiet = forms.BooleanField(label="Тихий запуск", required=False)


class DescriptionForm(OperationForm):
    kind = forms.ChoiceField(label="Вид записи", choices=[
        ("activity", "Активность"), ("quest", "Задание"),
        ("achievement", "Достижение"), ("perk", "Перк"),
        ("modifier", "Модификатор"),
    ])
    target = forms.ChoiceField(label="Запись")
    description = forms.CharField(
        label="Описание достижения", required=False, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    condition_text = forms.CharField(
        label="Описание условия / эффекта", required=False, max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean(self):
        data = super().clean()
        extra = set(self.data) - {*self.fields, "csrfmiddlewaretoken"}
        if extra:
            raise forms.ValidationError("Можно менять только тексты.")
        kind = data.get("kind")
        if kind == "activity":
            raise forms.ValidationError("У активности нет текстовых полей.")
        if not data.get("condition_text"):
            self.add_error("condition_text", "Описание условия обязательно.")
        if kind != "achievement" and data.get("description"):
            self.add_error("description", "У элемента нет общего описания.")
        return data

    def texts(self):
        fields = {"condition_text"}
        if self.cleaned_data["kind"] == "achievement":
            fields.add("description")
        return {key: self.cleaned_data[key] for key in fields}


class CreateSkillForm(ContentForm):
    description = DescribedContentForm.base_fields["description"]

    links = forms.CharField(widget=forms.HiddenInput, required=False,
                            initial="{}", max_length=20000)
    activities = ()

    def set_activities(self, activities):
        self.activities = [
            (key, item.name) for key, item in activities.items()
        ]

    def clean_links(self):
        try:
            links = loads(self.cleaned_data["links"] or "{}")
        except ValueError as exc:
            raise forms.ValidationError("Черновик связок повреждён.") from exc
        if not isinstance(links, dict) or not links:
            raise forms.ValidationError("Добавьте хотя бы одну активность.")
        known = dict(self.activities)
        for key, xp in links.items():
            if key not in known:
                raise forms.ValidationError("Неизвестная активность.")
            if type(xp) is not int or not 0 < xp <= 2**53 - 1:
                raise forms.ValidationError("Укажите положительное целое XP.")
        return links

    def definition(self):
        return {key: self.cleaned_data[key]
                for key in ("name", "description", "links")}


class FreezeForm(OperationForm):
    kind = forms.ChoiceField(label="Задания", choices=[
        ("daily", "Ежедневные"), ("weekly", "Еженедельные"),
    ])
    stock = forms.IntegerField(label="Текущий запас", min_value=0)
    maximum = forms.IntegerField(label="Максимальный запас", min_value=0)
    amount = forms.IntegerField(label="Количество за выдачу", min_value=0)
    interval = forms.IntegerField(
        label="Интервал (недель / месяцев)", min_value=1,
    )


COMMAND_FORMS = {
    "freezes": FreezeForm,
    "display": DisplayForm, "system": SystemForm,
    "description": DescriptionForm,
    "quick-activity": PinForm, "create-skill": CreateSkillForm,
    "create-activity": CreateActivityForm,
    "create-achievement": CreateAchievementForm,
    "create-quest": CreateQuestForm,
    "complete": CompletionForm, "pin": PinForm,
    "revise": RevisionForm, "batch": BatchForm,
    "perk": PerkForm, "level": LevelForm, "streak": StreakForm,
    "achievement": AchievementForm, "settings": SettingsForm,
    "rules": RulesForm,
}
