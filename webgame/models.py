"""Rebuildable SQLite projections; all gameplay writes go through services."""
from django.db import models


class RecordedEvent(models.Model):
    event_id = models.UUIDField(primary_key=True)
    seq = models.PositiveIntegerField(unique=True)
    data = models.JSONField()


class GameState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_applied_seq = models.PositiveIntegerField(default=0)
    data = models.JSONField(default=dict)


class Report(models.Model):
    period = models.CharField(max_length=30, primary_key=True)
    start = models.DateField()
    end = models.DateField()
    kind = models.CharField(max_length=10)
    data = models.JSONField()
