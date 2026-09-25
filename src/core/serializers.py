"""Serializers for the public and judge-facing API."""
from rest_framework import serializers

from .models import Project, Score


class ProjectSerializer(serializers.ModelSerializer):
    team = serializers.CharField(source="team.name", read_only=True)
    track = serializers.CharField(source="track.name", default="", read_only=True)

    class Meta:
        model = Project
        fields = [
            "external_id",
            "title",
            "summary",
            "team",
            "track",
            "repo_url",
            "live_url",
            "status",
            "submitted_at",
            "is_duplicate",
        ]


class ScoreValueField(serializers.Serializer):
    def to_representation(self, score):
        return {v.criterion.key: v.value for v in score.values.all()}


class JudgeScoreSerializer(serializers.ModelSerializer):
    project = serializers.CharField(source="project.external_id", read_only=True)
    project_title = serializers.CharField(source="project.title", read_only=True)
    criteria = serializers.SerializerMethodField()

    class Meta:
        model = Score
        fields = ["project", "project_title", "criteria", "comment", "created_at"]

    def get_criteria(self, score):
        return {v.criterion.key: v.value for v in score.values.all()}


class SubmitProjectSerializer(serializers.Serializer):
    """Input for a new submission. Kept small on purpose: the checker posts
    only title and summary. The deadline is enforced before we touch the
    body."""

    title = serializers.CharField(max_length=200)
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True)
