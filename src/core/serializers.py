"""Serializers for the public and judge-facing API."""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Comment, Project, Score


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

    @extend_schema_field(serializers.DictField(child=serializers.IntegerField()))
    def get_criteria(self, score):
        return {v.criterion.key: v.value for v in score.values.all()}


class SubmitProjectSerializer(serializers.Serializer):
    """Input for a new submission. Kept small on purpose: the checker posts
    only title and summary. The deadline is enforced before we touch the
    body."""

    title = serializers.CharField(max_length=200)
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True)


class VoteSerializer(serializers.Serializer):
    """Input for a community vote. A logged-in voter is keyed by their own
    identity, so voter_ref is read only for an anonymous visitor and ignored
    otherwise. That stops a signed-in voter casting many votes by changing the
    ref."""

    voter_ref = serializers.CharField(
        max_length=180, required=False, allow_blank=True,
        help_text=(
            "Stable per-visitor reference for an anonymous vote. Ignored for a "
            "logged-in voter, whose account identity is used instead."
        ),
    )


class CommentSerializer(serializers.ModelSerializer):
    """A comment as it is read back. Author is the display name or username,
    or 'anonymous' for a comment whose author row was removed."""

    author = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ["id", "author", "body", "created_at"]

    @extend_schema_field(serializers.CharField())
    def get_author(self, comment):
        if comment.author is None:
            return "anonymous"
        return comment.author.display_name or comment.author.username


class CommentCreateSerializer(serializers.Serializer):
    """Input for a new comment. Body is required and capped so a single caller
    cannot post an unbounded blob."""

    body = serializers.CharField(max_length=2000, trim_whitespace=True)
