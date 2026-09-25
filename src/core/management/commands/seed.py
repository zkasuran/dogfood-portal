"""Seed the portal from spec/fixtures.json and print the four dev auth
headers the acceptance checker attaches.

Idempotent: safe to run on every `docker compose up`. Everything is
created with get_or_create, so a second run does not duplicate rows.

The four printed tokens are DEV AND SEED ONLY. They exist so an offline
checker that never logs in can still attach a per-role header. Do not
ship them to a real deployment.
"""
import json
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from rest_framework.authtoken.models import Token

from core.models import (
    Criterion,
    Event,
    JudgeAssignment,
    Membership,
    Project,
    Role,
    Rubric,
    Score,
    ScoreValue,
    Team,
    Track,
    User,
)

# Fixture judges the known dev accounts map to. judge_a must have scores
# so its own-scores route returns real data.
JUDGE_A_FIXTURE = "jdg_07"   # 3 reviews in the fixture
JUDGE_B_FIXTURE = "jdg_02"   # a different judge, also with reviews

# Weighted rubric. Organizer-configurable; these are defensible defaults
# documented in JUDGING.md.
CRITERIA = [
    ("functionality", "Functionality", "0.4", 0),
    ("quality", "Quality", "0.4", 1),
    ("innovation", "Innovation", "0.2", 2),
]


def fixed_token(seed):
    """A deterministic 40-char token key. Readable prefix, zero padded."""
    return (seed + "0" * 40)[:40]


def parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt_timezone.utc
    )


def find_fixtures():
    env = os.environ.get("DOGFOOD_FIXTURES")
    base = Path(settings.BASE_DIR)
    candidates = [
        env,
        base.parent / "spec" / "fixtures.json",
        base / "fixtures.json",
        Path("/app/spec/fixtures.json"),
        Path.cwd() / "spec" / "fixtures.json",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise FileNotFoundError(
        "fixtures.json not found. Set DOGFOOD_FIXTURES or place it in spec/."
    )


class Command(BaseCommand):
    help = "Load fixtures.json and create the four dev accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        path = find_fixtures()
        data = json.loads(Path(path).read_text())
        self.stdout.write(f"seeding from {path}")

        event = self._event(data["event"])
        tracks = self._tracks(event, data["tracks"])
        rubric, criteria = self._rubric(event)
        judges = self._judges(data["judges"], tracks)
        self._teams_and_members(event, data["teams"])
        projects = self._projects(data["projects"], tracks)
        self._scores(data["scores"], judges, projects, criteria)

        known = self._known_accounts(judges, data)
        self._ensure_signing_key()
        self._print_auth(known)

    def _ensure_signing_key(self):
        """Generate the Ed25519 signing key on first boot. Load it if present.
        Idempotent. The private key is never logged or printed."""
        from core.judging import keys

        _, generated = keys.get_or_create_private_key(settings.SIGNING_KEY_PATH)
        self.stdout.write(
            "signing key generated" if generated else "signing key loaded"
        )

    def _event(self, ev):
        event, _ = Event.objects.get_or_create(
            external_id=ev["id"],
            defaults={
                "name": ev.get("name", "Event"),
                "submissions_close": parse_dt(ev["submissions_close"]),
            },
        )
        return event

    def _tracks(self, event, rows):
        tracks = {}
        for t in rows:
            track, _ = Track.objects.get_or_create(
                external_id=t["id"],
                defaults={"event": event, "name": t["name"]},
            )
            tracks[t["id"]] = track
        return tracks

    def _rubric(self, event):
        rubric, _ = Rubric.objects.get_or_create(
            event=event, defaults={"name": "Default rubric"}
        )
        criteria = {}
        for key, name, weight, order in CRITERIA:
            crit, _ = Criterion.objects.get_or_create(
                rubric=rubric, key=key,
                defaults={"name": name, "weight": weight, "order": order},
            )
            criteria[key] = crit
        return rubric, criteria

    def _judges(self, rows, tracks):
        judges = {}
        for j in rows:
            user, _ = User.objects.get_or_create(
                username=j["id"],
                defaults={
                    "email": j.get("email", ""),
                    "role": Role.JUDGE,
                    "external_id": j["id"],
                    "display_name": j.get("name", ""),
                },
            )
            for track_id in j.get("tracks", []):
                if track_id in tracks:
                    user.judge_tracks.add(tracks[track_id])
            judges[j["id"]] = user
        return judges

    def _teams_and_members(self, event, rows):
        for t in rows:
            team, _ = Team.objects.get_or_create(
                external_id=t["id"],
                defaults={"event": event, "name": t["name"]},
            )
            for email in t.get("members", []):
                member = self._participant(email)
                Membership.objects.get_or_create(team=team, user=member)

    def _participant(self, email):
        user, created = User.objects.get_or_create(
            username=email,
            defaults={"email": email, "role": Role.PARTICIPANT},
        )
        return user

    def _projects(self, rows, tracks):
        projects = {}
        seen_repo = {}
        for p in rows:
            track = tracks.get(p.get("track"))
            team = Team.objects.filter(external_id=p["team"]).first()
            submitted = parse_dt(p.get("submitted_at"))
            dup_key = (p.get("team"), p.get("repo_url"))
            is_dup = dup_key in seen_repo
            project, _ = Project.objects.get_or_create(
                external_id=p["id"],
                defaults={
                    "team": team,
                    "track": track,
                    "title": p.get("title", ""),
                    "summary": p.get("summary", ""),
                    "repo_url": p.get("repo_url", ""),
                    "submitted_at": submitted,
                    "status": Project.Status.SUBMITTED,
                    "is_duplicate": is_dup,
                },
            )
            if is_dup and project.duplicate_of_id is None:
                project.duplicate_of = seen_repo[dup_key]
                project.is_duplicate = True
                project.save(update_fields=["duplicate_of", "is_duplicate"])
            else:
                seen_repo.setdefault(dup_key, project)
            projects[p["id"]] = project
        return projects

    def _scores(self, rows, judges, projects, criteria):
        for s in rows:
            judge = judges.get(s["judge"])
            project = projects.get(s["project"])
            if judge is None or project is None:
                continue
            JudgeAssignment.objects.get_or_create(judge=judge, project=project)
            score, _ = Score.objects.get_or_create(
                judge=judge, project=project,
                defaults={"comment": s.get("comment", "")},
            )
            for key, value in (s.get("criteria") or {}).items():
                crit = criteria.get(key)
                if crit is None:
                    continue
                ScoreValue.objects.get_or_create(
                    score=score, criterion=crit, defaults={"value": value}
                )

    def _known_accounts(self, judges, data):
        # organizer and admin are portal accounts we create, not fixture rows.
        organizer, _ = User.objects.get_or_create(
            username="organizer",
            defaults={
                "email": "organizer@dogfood.local",
                "role": Role.ORGANIZER,
                "display_name": "Demo Organizer",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        organizer.set_password("organizer")
        organizer.save()

        admin, _ = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@dogfood.local",
                "role": Role.ADMIN,
                "display_name": "Demo Admin",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        admin.set_password("admin")
        admin.save()

        judge_a = judges[JUDGE_A_FIXTURE]
        judge_b = judges[JUDGE_B_FIXTURE]

        # a real team member becomes the known participant account
        first_member = data["teams"][0]["members"][0]
        participant = User.objects.get(username=first_member)

        accounts = {
            "organizer": (organizer, fixed_token("organizer")),
            "judge_a": (judge_a, fixed_token("judgea")),
            "judge_b": (judge_b, fixed_token("judgeb")),
            "participant": (participant, fixed_token("participant")),
        }
        for user, key in accounts.values():
            Token.objects.filter(user=user).exclude(key=key).delete()
            Token.objects.update_or_create(user=user, defaults={"key": key})
        return accounts

    def _print_auth(self, accounts):
        out = self.stdout
        out.write("")
        out.write("seeded. dev/seed-only test logins (paste into .dogfood.toml [auth]):")
        out.write("")
        for name in ("organizer", "judge_a", "judge_b", "participant"):
            user, key = accounts[name]
            out.write(f'  {name:<11} = "Authorization: Token {key}"')
        out.write("")
        out.write("These four tokens are dev and seed only. They exist so the")
        out.write("offline acceptance checker can attach a per-role header.")
