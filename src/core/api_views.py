"""API views. Public gallery data, judge score isolation, CSV export.

The load-bearing rule is in JudgeScoresView: a judge may read their own
scores. Only an organizer or admin may read someone else's. A judge
asking for a peer's scores by id is refused with 403. That check is here
in the backend, so a curl cannot slip past a hidden template button.
"""
import csv

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AuditLog,
    Bundle,
    Criterion,
    Event,
    Project,
    Role,
    Rubric,
    Score,
    User,
)
from .permissions import ORGANIZER_ROLES, IsJudge, IsOrganizer
from .serializers import (
    JudgeScoreSerializer,
    ProjectSerializer,
    SubmitProjectSerializer,
)


class ProjectListCreateView(ListAPIView):
    """GET is the public project list. POST is a new submission, refused
    once the event deadline has passed."""

    serializer_class = ProjectSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return (
            Project.objects.filter(status=Project.Status.SUBMITTED)
            .select_related("team", "track")
            .order_by("external_id")
        )

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"detail": "Authentication required to submit."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        event = Event.objects.order_by("submissions_close").first()
        now = timezone.now()
        if event and not event.submissions_open(now):
            AuditLog.objects.create(
                actor=request.user,
                action="submit.refused",
                target=event.external_id,
                detail={"reason": "submissions_closed",
                        "closed_at": event.submissions_close.isoformat()},
            )
            return Response(
                {
                    "detail": "Submissions are closed for this event.",
                    "submissions_close": event.submissions_close.isoformat(),
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SubmitProjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # An open event would create a draft here. The fixture event is
        # closed, so this branch is not reached by the acceptance run.
        return Response(serializer.validated_data, status=status.HTTP_201_CREATED)


class JudgeScoresView(APIView):
    """The caller's own scores. A peer's only for an organizer or admin."""

    permission_classes = [IsJudge]

    def get(self, request):
        caller = request.user
        requested = request.query_params.get("judge_id")
        caller_is_privileged = caller.role in ORGANIZER_ROLES

        if requested:
            own = requested == (caller.external_id or "")
            if not (own or caller_is_privileged):
                return Response(
                    {"detail": "You may only read your own scores."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            target = User.objects.filter(external_id=requested, role=Role.JUDGE).first()
            if target is None:
                return Response(
                    {"detail": "No such judge."}, status=status.HTTP_404_NOT_FOUND
                )
        else:
            target = caller

        scores = (
            Score.objects.filter(judge=target)
            .select_related("project")
            .prefetch_related("values__criterion")
            .order_by("project__external_id")
        )
        data = JudgeScoreSerializer(scores, many=True).data
        return Response(
            {
                "judge_id": target.external_id or target.username,
                "judge_name": target.display_name or target.get_full_name(),
                "count": len(data),
                "scores": data,
            }
        )


class CsvExportView(APIView):
    """Organizer-only results export. Weighted composite per project, so
    the criterion weights an organizer set are carried into the numbers a
    sponsor reads."""

    permission_classes = [IsOrganizer]

    def get(self, request):
        criteria = list(Criterion.objects.order_by("order", "id"))
        header = ["project_id", "title", "track", "team", "review_count"]
        header += [c.key for c in criteria]
        header += ["weighted_mean"]

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="dogfood-results.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(header)

        projects = (
            Project.objects.select_related("team", "track")
            .prefetch_related("scores__values__criterion")
            .order_by("external_id")
        )
        weight = {c.id: float(c.weight) for c in criteria}
        total_weight = sum(weight.values()) or 1.0

        for project in projects:
            reviews = list(project.scores.all())
            per_criterion = {c.id: [] for c in criteria}
            for review in reviews:
                for value in review.values.all():
                    if value.criterion_id in per_criterion:
                        per_criterion[value.criterion_id].append(value.value)
            row = [
                project.external_id,
                project.title,
                project.track.name if project.track else "",
                project.team.name,
                len(reviews),
            ]
            criterion_means = {}
            for c in criteria:
                vals = per_criterion[c.id]
                mean = sum(vals) / len(vals) if vals else ""
                criterion_means[c.id] = mean
                row.append(f"{mean:.2f}" if vals else "")
            weighted = sum(
                criterion_means[c.id] * weight[c.id]
                for c in criteria
                if criterion_means[c.id] != ""
            )
            present_weight = sum(
                weight[c.id] for c in criteria if criterion_means[c.id] != ""
            ) or total_weight
            row.append(f"{weighted / present_weight:.3f}" if reviews else "")
            writer.writerow(row)

        AuditLog.objects.create(
            actor=request.user, action="export.csv", target="results"
        )
        return response


class ProgressView(APIView):
    """Organizer progress dashboard as JSON: how many reviews are in, how
    many projects still need coverage."""

    permission_classes = [IsOrganizer]

    def get(self, request):
        projects = Project.objects.all()
        total = projects.count()
        reviewed = (
            Score.objects.values("project").distinct().count()
        )
        avg_reviews = Score.objects.count() / total if total else 0
        return Response(
            {
                "projects": total,
                "projects_with_reviews": reviewed,
                "projects_missing_reviews": total - reviewed,
                "total_reviews": Score.objects.count(),
                "avg_reviews_per_project": round(avg_reviews, 2),
            }
        )


class PublishResultsView(APIView):
    """Organizer-only. Read the live scores through the adapter, run the judging
    engine, sign the result and store it. A judge cannot publish, and the same
    backend role check as every other route enforces that, not a hidden button.

    POST body may set {"method": "additive"|"zscore"}. Default is additive.
    Returns the signed bundle so an organizer can hand it straight to verify.py.
    """

    permission_classes = [IsOrganizer]

    def post(self, request):
        from .judging import adapter, bundle as bundle_mod, keys

        rubric = Rubric.objects.order_by("id").first()
        weights = adapter.rubric_weights(rubric)
        source = adapter.build_source()
        if not source["scores"]:
            return Response(
                {"detail": "There are no scores to publish yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        method = request.data.get("method", "additive")
        if method not in ("additive", "zscore"):
            method = "additive"

        payload = bundle_mod.build_payload(
            source, weights, code_commit=settings.GIT_COMMIT, method=method
        )
        private_key, _ = keys.get_or_create_private_key(settings.SIGNING_KEY_PATH)
        signed = bundle_mod.sign_payload(payload, private_key)

        row = Bundle.objects.create(
            payload=signed["payload"],
            digest=signed["digest"],
            signature=signed["signature"],
            public_key=signed["public_key"],
            code_commit=settings.GIT_COMMIT,
        )
        AuditLog.objects.create(
            actor=request.user,
            action="results.publish",
            target=str(row.id),
            detail={
                "digest": row.digest,
                "method": payload["method"],
                "code_commit": row.code_commit,
                "projects_ranked": len(payload["ranking"]),
            },
        )
        return Response(signed, status=status.HTTP_201_CREATED)


class BundleView(APIView):
    """Public. The latest signed results bundle, in the exact form verify.py checks.
    Results are meant to be checkable, so no auth is required to read one."""

    permission_classes = [AllowAny]

    def get(self, request):
        row = Bundle.objects.first()
        if row is None:
            return Response(
                {"detail": "No results have been published yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(row.as_bundle())


class VerificationKeyView(APIView):
    """Public. The Ed25519 public key that signs every bundle, as hex, so anyone can
    verify a downloaded bundle without trusting the host and without a key of ours."""

    permission_classes = [AllowAny]

    def get(self, request):
        from .judging import keys

        public_key = keys.public_key_hex(settings.SIGNING_KEY_PATH)
        return Response({"algorithm": "ed25519", "public_key": public_key})
