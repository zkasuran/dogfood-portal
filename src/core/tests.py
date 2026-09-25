"""Tests for the load-bearing behaviour: role isolation, deadline
enforcement and the public gallery. These mirror the seven acceptance
checks but assert them directly against the API, so a regression is caught
before the checker runs."""
from datetime import timezone as dt_timezone
from datetime import datetime

from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core.models import (
    Criterion,
    Event,
    Project,
    Role,
    Rubric,
    Score,
    ScoreValue,
    Team,
    Track,
    User,
)


class SpineTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            external_id="evt_01",
            name="Sample Hack",
            submissions_close=datetime(2026, 3, 1, 18, 0, tzinfo=dt_timezone.utc),
        )
        cls.track = Track.objects.create(
            external_id="trk_01", event=cls.event, name="Developer tools"
        )
        cls.team = Team.objects.create(
            external_id="tm_01", event=cls.event, name="Nightshift"
        )
        cls.project = Project.objects.create(
            external_id="prj_01",
            team=cls.team,
            track=cls.track,
            title="Glass Signal",
            summary="One line.",
            status=Project.Status.SUBMITTED,
        )
        cls.rubric = Rubric.objects.create(event=cls.event)
        cls.crit = Criterion.objects.create(
            rubric=cls.rubric, key="functionality", name="Functionality", weight="0.5"
        )

        def make(username, role, external_id=None):
            user = User.objects.create(
                username=username, role=role, external_id=external_id
            )
            token = Token.objects.create(user=user)
            return user, token.key

        cls.judge_a, cls.tok_a = make("jdg_07", Role.JUDGE, "jdg_07")
        cls.judge_b, cls.tok_b = make("jdg_02", Role.JUDGE, "jdg_02")
        cls.participant, cls.tok_p = make("priya@example.org", Role.PARTICIPANT)
        cls.organizer, cls.tok_o = make("organizer", Role.ORGANIZER)

        score = Score.objects.create(judge=cls.judge_a, project=cls.project)
        ScoreValue.objects.create(score=score, criterion=cls.crit, value=4)

    def auth(self, key):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {key}")

    # T1 -----------------------------------------------------------------
    def test_gallery_public_and_shows_fixture(self):
        self.client.credentials()
        resp = self.client.get("/projects")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Glass Signal", resp.content.decode())

    def test_submit_after_deadline_refused(self):
        self.auth(self.tok_p)
        resp = self.client.post(
            "/api/projects", {"title": "late", "summary": "x"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_submit_anonymous_unauthorized(self):
        self.client.credentials()
        resp = self.client.post("/api/projects", {"title": "x"}, format="json")
        self.assertEqual(resp.status_code, 401)

    # T2 -----------------------------------------------------------------
    def test_judge_reads_own_scores(self):
        self.auth(self.tok_a)
        resp = self.client.get("/api/judge/scores")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

    def test_judge_cannot_read_peer_scores(self):
        # the load-bearing check: judge_b asks for judge_a by id
        self.auth(self.tok_b)
        resp = self.client.get("/api/judge/scores?judge_id=jdg_07")
        self.assertEqual(resp.status_code, 403)

    def test_participant_blocked_from_judge_route(self):
        self.auth(self.tok_p)
        resp = self.client.get("/api/judge/scores")
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_blocked_from_judge_route(self):
        self.client.credentials()
        resp = self.client.get("/api/judge/scores")
        self.assertIn(resp.status_code, (401, 403))

    def test_organizer_may_read_peer_scores(self):
        self.auth(self.tok_o)
        resp = self.client.get("/api/judge/scores?judge_id=jdg_07")
        self.assertEqual(resp.status_code, 200)

    def test_csv_export_organizer_only(self):
        self.auth(self.tok_o)
        resp = self.client.get("/api/export/results.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(",", resp.content.decode().splitlines()[0])

    def test_csv_export_refuses_judge(self):
        self.auth(self.tok_a)
        resp = self.client.get("/api/export/results.csv")
        self.assertEqual(resp.status_code, 403)

    def test_health_public(self):
        self.client.credentials()
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
