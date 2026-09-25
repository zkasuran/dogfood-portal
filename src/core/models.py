"""Data model for the DOGFOOD judging portal.

The fixture file is input, not our schema. We load it into normalised
tables we can defend: events own tracks, prizes and a rubric; teams own
projects; judges score projects against weighted criteria. Every row that
came from a fixture keeps its original string id in `external_id`, so an
import round-trips and an export can be matched back to the source.
"""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Role(models.TextChoices):
    VISITOR = "visitor", "Visitor"
    PARTICIPANT = "participant", "Participant"
    JUDGE = "judge", "Judge"
    ORGANIZER = "organizer", "Organizer"
    ADMIN = "admin", "Admin"


class User(AbstractUser):
    """A portal account with one role.

    Role is the security boundary the API enforces. `external_id` links a
    judge or participant back to the fixture record it was seeded from.
    """

    role = models.CharField(
        max_length=16, choices=Role.choices, default=Role.PARTICIPANT
    )
    external_id = models.CharField(
        max_length=64, blank=True, null=True, unique=True,
        help_text="Original fixture id, e.g. jdg_07. Null for accounts we create.",
    )
    display_name = models.CharField(max_length=200, blank=True)
    # Which tracks a judge is eligible to review. Empty for non-judges.
    judge_tracks = models.ManyToManyField(
        "Track", blank=True, related_name="judges"
    )

    def __str__(self):
        return f"{self.username} ({self.role})"


class Event(models.Model):
    external_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    submissions_close = models.DateTimeField()
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    def submissions_open(self, now):
        """True while the event still accepts submissions."""
        return now <= self.submissions_close


class Prize(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    place = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    label = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["place"]
        unique_together = [("event", "place")]

    def __str__(self):
        return f"{self.event.name} #{self.place}"


class Track(models.Model):
    external_id = models.CharField(max_length=64, unique=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Team(models.Model):
    external_id = models.CharField(max_length=64, unique=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Membership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )

    class Meta:
        unique_together = [("team", "user")]

    def __str__(self):
        return f"{self.user.username} in {self.team.name}"


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"

    external_id = models.CharField(max_length=64, unique=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="projects")
    track = models.ForeignKey(
        Track, on_delete=models.SET_NULL, null=True, blank=True, related_name="projects"
    )
    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    thumbnail_url = models.URLField(blank=True)
    repo_url = models.URLField(blank=True)
    demo_video_url = models.URLField(blank=True)
    live_url = models.URLField(blank=True)
    tech_tags = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Duplicate submissions are allowed to exist; we flag them rather than
    # dropping data, so an organizer can decide what to do.
    is_duplicate = models.BooleanField(default=False)
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="duplicates",
    )

    class Meta:
        ordering = ["external_id"]

    def __str__(self):
        return self.title


class JudgeAssignment(models.Model):
    """A judge is assigned a project in a batch. Assignments are disjoint,
    so no judge is handed a project a peer is already reviewing in the same
    batch. No judge sees a peer's ballot."""

    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assignments"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="assignments"
    )
    batch = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [("judge", "project")]

    def __str__(self):
        return f"{self.judge.username} -> {self.project.title}"


class Rubric(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rubrics")
    name = models.CharField(max_length=200, default="Default rubric")

    def __str__(self):
        return f"{self.name} ({self.event.name})"


class Criterion(models.Model):
    """A weighted scoring dimension. Weight is what most platforms lack:
    the organizer sets how much each dimension counts before scores are
    combined."""

    rubric = models.ForeignKey(
        Rubric, on_delete=models.CASCADE, related_name="criteria"
    )
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    weight = models.DecimalField(max_digits=6, decimal_places=3, default=1)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        unique_together = [("rubric", "key")]

    def __str__(self):
        return f"{self.name} (w={self.weight})"


class Score(models.Model):
    """One judge's review of one project. Values live in ScoreValue rows,
    one per criterion, so a rubric can change without a schema change."""

    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scores"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="scores"
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("judge", "project")]

    def __str__(self):
        return f"{self.judge.username} on {self.project.title}"


class ScoreValue(models.Model):
    score = models.ForeignKey(Score, on_delete=models.CASCADE, related_name="values")
    criterion = models.ForeignKey(
        Criterion, on_delete=models.CASCADE, related_name="values"
    )
    value = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    class Meta:
        unique_together = [("score", "criterion")]

    def __str__(self):
        return f"{self.criterion.key}={self.value}"


class Vote(models.Model):
    """Community vote (T3). Kept in the schema so the spine is coherent."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="votes")
    voter_ref = models.CharField(max_length=200)
    weight = models.DecimalField(max_digits=6, decimal_places=3, default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "voter_ref")]


class Comment(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="comments",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class AuditLog(models.Model):
    """Organizer-readable trail. No database client needed to read it."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=100)
    target = models.CharField(max_length=200, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]

    def __str__(self):
        return f"{self.action} {self.target}"
