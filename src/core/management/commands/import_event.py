"""Load an event exported by export_event into the current database.

The companion to export_event. Point it at a JSON dump and it recreates the
event and every row that hung off it: prizes, tracks, teams and members,
projects (the duplicate is relinked after both rows exist, so a self-referential
duplicate_of never fails), the weighted rubric and its criteria, judge
assignments, scores with per-criterion values, votes and comments, plus the judge
and participant accounts.

    python manage.py import_event event.json

Idempotent: every row is matched on its natural key with get_or_create, so a
second run does not duplicate. Run it against a fresh database (migrated, not
seeded) to reproduce an event somewhere new. The dev auth tokens and portal
admin accounts are not in the dump; a fresh instance gets those from `seed`.
See MIGRATION.md.
"""
import json
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Comment,
    Criterion,
    Event,
    JudgeAssignment,
    Membership,
    Prize,
    Project,
    Rubric,
    Score,
    ScoreValue,
    Team,
    Track,
    User,
    Vote,
)

EXPECTED_FORMAT = "dogfood-event-export"


def parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(dt_timezone.utc)


def to_decimal(value, default="1"):
    return Decimal(value) if value is not None else Decimal(default)


class Command(BaseCommand):
    help = "Load an event JSON dump (from export_event) into this database."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the event JSON file.")

    @transaction.atomic
    def handle(self, *args, **options):
        with open(options["path"], encoding="utf-8") as fh:
            data = json.load(fh)
        fmt = data.get("format")
        if fmt != EXPECTED_FORMAT:
            raise CommandError(
                f"Not a {EXPECTED_FORMAT} file (found format={fmt!r})."
            )

        event = self._event(data["event"])
        tracks = self._tracks(event, data.get("tracks", []))
        self._users(data.get("users", []), tracks)
        teams = self._teams(event, data.get("teams", []))
        self._prizes(event, data.get("prizes", []))
        projects = self._projects(teams, tracks, data.get("projects", []))
        self._relink_duplicates(projects, data.get("projects", []))
        criteria = self._rubrics(event, data.get("rubrics", []))
        self._assignments(projects, data.get("assignments", []))
        self._scores(projects, criteria, data.get("scores", []))
        self._votes(projects, data.get("votes", []))
        self._comments(projects, data.get("comments", []))
        self._report(event)

    def _event(self, ev):
        event, _ = Event.objects.get_or_create(
            external_id=ev["external_id"],
            defaults={
                "name": ev.get("name", ""),
                "submissions_close": parse_dt(ev.get("submissions_close")),
                "starts_at": parse_dt(ev.get("starts_at")),
                "ends_at": parse_dt(ev.get("ends_at")),
            },
        )
        return event

    def _tracks(self, event, rows):
        tracks = {}
        for t in rows:
            track, _ = Track.objects.get_or_create(
                external_id=t["external_id"],
                defaults={"event": event, "name": t["name"]},
            )
            tracks[t["external_id"]] = track
        return tracks

    def _users(self, rows, tracks):
        for u in rows:
            user, created = User.objects.get_or_create(
                username=u["username"],
                defaults={
                    "email": u.get("email", ""),
                    "role": u.get("role", "participant"),
                    "external_id": u.get("external_id"),
                    "display_name": u.get("display_name", ""),
                    "is_staff": u.get("is_staff", False),
                    "is_superuser": u.get("is_superuser", False),
                },
            )
            # Password hashes travel so accounts keep working after a move. The
            # seed accounts have no usable password, so this is usually empty.
            if created and u.get("password"):
                user.password = u["password"]
                user.save(update_fields=["password"])
            for track_ext in u.get("judge_tracks", []):
                track = tracks.get(track_ext)
                if track is not None:
                    user.judge_tracks.add(track)

    def _teams(self, event, rows):
        teams = {}
        for t in rows:
            team, _ = Team.objects.get_or_create(
                external_id=t["external_id"],
                defaults={"event": event, "name": t["name"]},
            )
            teams[t["external_id"]] = team
            for username in t.get("members", []):
                member = User.objects.filter(username=username).first()
                if member is not None:
                    Membership.objects.get_or_create(team=team, user=member)
        return teams

    def _prizes(self, event, rows):
        for p in rows:
            Prize.objects.get_or_create(
                event=event, place=p["place"],
                defaults={
                    "amount": to_decimal(p.get("amount"), default="0"),
                    "label": p.get("label", ""),
                },
            )

    def _projects(self, teams, tracks, rows):
        # First pass: create every project with duplicate_of unset. The
        # self-referential link is set in _relink_duplicates once both rows
        # exist, so a duplicate never references a project that is not there yet.
        projects = {}
        for p in rows:
            project, _ = Project.objects.get_or_create(
                external_id=p["external_id"],
                defaults={
                    "team": teams.get(p["team"]),
                    "track": tracks.get(p.get("track")),
                    "title": p.get("title", ""),
                    "summary": p.get("summary", ""),
                    "description": p.get("description", ""),
                    "thumbnail_url": p.get("thumbnail_url", ""),
                    "repo_url": p.get("repo_url", ""),
                    "demo_video_url": p.get("demo_video_url", ""),
                    "live_url": p.get("live_url", ""),
                    "tech_tags": p.get("tech_tags", []),
                    "status": p.get("status", Project.Status.SUBMITTED),
                    "submitted_at": parse_dt(p.get("submitted_at")),
                    "is_duplicate": p.get("is_duplicate", False),
                },
            )
            projects[p["external_id"]] = project
        return projects

    def _relink_duplicates(self, projects, rows):
        for p in rows:
            canon_ext = p.get("duplicate_of")
            if not canon_ext:
                continue
            dup = projects.get(p["external_id"])
            canon = projects.get(canon_ext)
            if dup is None or canon is None:
                continue
            dup.is_duplicate = True
            dup.duplicate_of = canon
            dup.save(update_fields=["is_duplicate", "duplicate_of"])

    def _rubrics(self, event, rows):
        """Recreate rubrics and criteria. Returns
        {(rubric_index, criterion_key): Criterion} so a score value resolves."""
        criteria = {}
        for r_index, r in enumerate(rows):
            rubric, _ = Rubric.objects.get_or_create(
                event=event, name=r.get("name", "Default rubric")
            )
            for c in r.get("criteria", []):
                crit, _ = Criterion.objects.get_or_create(
                    rubric=rubric, key=c["key"],
                    defaults={
                        "name": c.get("name", c["key"]),
                        "weight": to_decimal(c.get("weight")),
                        "order": c.get("order", 0),
                    },
                )
                criteria[(r_index, c["key"])] = crit
        return criteria

    def _assignments(self, projects, rows):
        for a in rows:
            judge = User.objects.filter(username=a["judge"]).first()
            project = projects.get(a["project"])
            if judge is None or project is None:
                continue
            JudgeAssignment.objects.get_or_create(
                judge=judge, project=project,
                defaults={"batch": a.get("batch", 1)},
            )

    def _scores(self, projects, criteria, rows):
        for s in rows:
            judge = User.objects.filter(username=s["judge"]).first()
            project = projects.get(s["project"])
            if judge is None or project is None:
                continue
            score, created = Score.objects.get_or_create(
                judge=judge, project=project,
                defaults={"comment": s.get("comment", "")},
            )
            # created_at is auto_now_add, so preserve the source time explicitly.
            created_at = parse_dt(s.get("created_at"))
            if created and created_at:
                Score.objects.filter(pk=score.pk).update(created_at=created_at)
            for v in s.get("values", []):
                crit = criteria.get((v.get("rubric", 0), v.get("criterion")))
                if crit is None:
                    continue
                ScoreValue.objects.get_or_create(
                    score=score, criterion=crit,
                    defaults={"value": v["value"]},
                )

    def _votes(self, projects, rows):
        for v in rows:
            project = projects.get(v["project"])
            if project is None:
                continue
            vote, created = Vote.objects.get_or_create(
                project=project, voter_ref=v["voter_ref"],
                defaults={"weight": to_decimal(v.get("weight"))},
            )
            created_at = parse_dt(v.get("created_at"))
            if created and created_at:
                Vote.objects.filter(pk=vote.pk).update(created_at=created_at)

    def _comments(self, projects, rows):
        for c in rows:
            project = projects.get(c["project"])
            if project is None:
                continue
            author = None
            if c.get("author"):
                author = User.objects.filter(username=c["author"]).first()
            comment, created = Comment.objects.get_or_create(
                project=project, author=author, body=c.get("body", ""),
            )
            created_at = parse_dt(c.get("created_at"))
            if created and created_at:
                Comment.objects.filter(pk=comment.pk).update(
                    created_at=created_at
                )

    def _report(self, event):
        team_ids = list(event.teams.values_list("id", flat=True))
        project_ids = list(
            Project.objects.filter(team__in=team_ids).values_list(
                "id", flat=True
            )
        )
        self.stderr.write(
            "imported event {ev}: {tm} teams, {pr} projects ({dup} duplicate), "
            "{sc} scores, {sv} score values".format(
                ev=event.external_id,
                tm=len(team_ids),
                pr=len(project_ids),
                dup=Project.objects.filter(
                    team__in=team_ids, is_duplicate=True
                ).count(),
                sc=Score.objects.filter(project__in=project_ids).count(),
                sv=ScoreValue.objects.filter(
                    score__project__in=project_ids
                ).count(),
            )
        )
