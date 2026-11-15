"""Structural validation only; reward calculations belong to the core."""
from fractions import Fraction
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


COMMAND_FORMS = {
    "complete": ActivityForm, "revise": RevisionForm, "batch": BatchForm,
    "perk": PerkForm, "level": LevelForm, "streak": StreakForm,
    "achievement": AchievementForm, "settings": SettingsForm,
    "rules": RulesForm,
}
