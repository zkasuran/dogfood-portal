"""Export one event and every row that hangs off it to portable JSON.

Companion to import_event. Together they are the migration path in and out:
dump an event from one instance, load it into a fresh instance and the same
projects, teams, judges, scores and rubric come back. The dump is plain JSON on
stdout, so it pipes straight to a file:

    python manage.py export_event evt_01 > event.json

The graph is everything reachable from the event: its prizes, tracks, teams and
their members, projects (duplicates kept and flagged), the rubric and its
weighted criteria, judge assignments, scores with per-criterion values, votes
and comments, plus the judge and participant accounts the event needs. Portal
admin accounts and the dev auth tokens are not event data, so they do not travel.
A fresh instance gets those from `seed`. See MIGRATION.md.
"""
import json
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.models import (
    Comment,
    Event,
    JudgeAssignment,
    Project,
    Score,
    User,
    Vote,
)

FORMAT = "dogfood-event-export"
VERSION = 1


def dt(value):
    """An ISO string for a datetime, else None."""
    return value.isoformat() if value else None


def dec(value):
    """A Decimal (or number) as a string, so no precision is lost in JSON."""
    return None if value is None else str(value)


class Command(BaseCommand):
    help = "Dump one event and all related rows to portable JSON on stdout."

    def add_arguments(self, parser):
        parser.add_argument(
            "event", help="The event external_id to export, e.g. evt_01."
        )
        parser.add_argument(
            "--output", "-o", default=None,
            help="Write to this file instead of stdout.",
        )
        parser.add_argument(
            "--indent", type=int, default=None,
            help="Pretty-print with this indent. Default is compact.",
        )

    def handle(self, *args, **options):
        event = Event.objects.filter(external_id=options["event"]).first()
        if event is None:
            known = ", ".join(
                Event.objects.values_list("external_id", flat=True)
            ) or "none"
            raise CommandError(
                f"No event with external_id {options['event']!r}. Known: {known}."
            )

        data = self._dump(event)
        text = json.dumps(data, indent=options["indent"], ensure_ascii=False)

        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as fh:
                fh.write(text)
            self.stderr.write(f"wrote {options['output']}")
        else:
            self.stdout.write(text)

        self._report(data)

    def _dump(self, event):
        tracks = list(event.tracks.order_by("external_id"))
        teams = list(event.teams.order_by("external_id"))
        team_ids = [t.id for t in teams]
        projects = list(
            Project.objects.filter(team__in=team_ids)
            .select_related("team", "track", "duplicate_of")
            .order_by("external_id")
        )
        project_ids = [p.id for p in projects]
        rubrics = list(event.rubrics.order_by("id"))

        # Users the event needs: judges (scoped to its tracks or holding a score
        # or an assignment here) and participants (members of its teams). Portal
        # admin accounts are not event data, so they are left out.
        users = list(
            User.objects.filter(
                Q(judge_tracks__in=tracks)
                | Q(scores__project__in=project_ids)
                | Q(assignments__project__in=project_ids)
                | Q(memberships__team__in=team_ids)
            )
            .distinct()
            .order_by("username")
            .prefetch_related("judge_tracks")
        )

        # criterion id -> {rubric index, criterion key}, so a score value can
        # name its criterion even if two rubrics ever share a key.
        crit_ref = {}
        rubric_out = []
        for r_index, rubric in enumerate(rubrics):
            crit_rows = []
            for c in rubric.criteria.order_by("order", "id"):
                crit_ref[c.id] = {"rubric": r_index, "criterion": c.key}
                crit_rows.append(
                    {"key": c.key, "name": c.name,
                     "weight": dec(c.weight), "order": c.order}
                )
            rubric_out.append({"name": rubric.name, "criteria": crit_rows})

        return {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": datetime.now(dt_timezone.utc).isoformat(),
            "event": {
                "external_id": event.external_id,
                "name": event.name,
                "submissions_close": dt(event.submissions_close),
                "starts_at": dt(event.starts_at),
                "ends_at": dt(event.ends_at),
            },
            "prizes": [
                {"place": p.place, "amount": dec(p.amount), "label": p.label}
                for p in event.prizes.order_by("place")
            ],
            "tracks": [
                {"external_id": t.external_id, "name": t.name} for t in tracks
            ],
            "users": [self._user(u) for u in users],
            "teams": [self._team(t) for t in teams],
            "projects": [self._project(p) for p in projects],
            "rubrics": rubric_out,
            "assignments": [
                {"judge": a.judge.username, "project": a.project.external_id,
                 "batch": a.batch}
                for a in JudgeAssignment.objects.filter(project__in=project_ids)
                .select_related("judge", "project")
                .order_by("judge__username", "project__external_id")
            ],
            "scores": [
                self._score(s, crit_ref)
                for s in Score.objects.filter(project__in=project_ids)
                .select_related("judge", "project")
                .prefetch_related("values")
                .order_by("judge__username", "project__external_id")
            ],
            "votes": [
                {"project": v.project.external_id, "voter_ref": v.voter_ref,
                 "weight": dec(v.weight), "created_at": dt(v.created_at)}
                for v in Vote.objects.filter(project__in=project_ids)
                .select_related("project").order_by("id")
            ],
            "comments": [
                {"project": c.project.external_id,
                 "author": c.author.username if c.author else None,
                 "body": c.body, "created_at": dt(c.created_at)}
                for c in Comment.objects.filter(project__in=project_ids)
                .select_related("project", "author").order_by("id")
            ],
        }

    def _user(self, u):
        return {
            "username": u.username,
            "email": u.email,
            "role": u.role,
            "external_id": u.external_id,
            "display_name": u.display_name,
            "is_staff": u.is_staff,
            "is_superuser": u.is_superuser,
            "password": u.password,
            "judge_tracks": sorted(t.external_id for t in u.judge_tracks.all()),
        }

    def _team(self, t):
        return {
            "external_id": t.external_id,
            "name": t.name,
            "members": sorted(
                m.user.username for m in t.memberships.select_related("user")
            ),
        }

    def _project(self, p):
        return {
            "external_id": p.external_id,
            "team": p.team.external_id,
            "track": p.track.external_id if p.track else None,
            "title": p.title,
            "summary": p.summary,
            "description": p.description,
            "thumbnail_url": p.thumbnail_url,
            "repo_url": p.repo_url,
            "demo_video_url": p.demo_video_url,
            "live_url": p.live_url,
            "tech_tags": p.tech_tags,
            "status": p.status,
            "submitted_at": dt(p.submitted_at),
            "is_duplicate": p.is_duplicate,
            "duplicate_of": p.duplicate_of.external_id if p.duplicate_of else None,
        }

    def _score(self, s, crit_ref):
        values = []
        for v in s.values.all():
            ref = crit_ref.get(v.criterion_id)
            if ref is None:
                continue
            values.append({**ref, "value": v.value})
        return {
            "judge": s.judge.username,
            "project": s.project.external_id,
            "comment": s.comment,
            "created_at": dt(s.created_at),
            "values": values,
        }

    def _report(self, data):
        self.stderr.write(
            "exported event {ev}: {u} users, {tr} tracks, {tm} teams, "
            "{pr} projects ({dup} duplicate), {asg} assignments, {sc} scores, "
            "{sv} score values".format(
                ev=data["event"]["external_id"],
                u=len(data["users"]),
                tr=len(data["tracks"]),
                tm=len(data["teams"]),
                pr=len(data["projects"]),
                dup=sum(1 for p in data["projects"] if p["is_duplicate"]),
                asg=len(data["assignments"]),
                sc=len(data["scores"]),
                sv=sum(len(s["values"]) for s in data["scores"]),
            )
        )
