"""Tests for the T3 community and integrity features: one-vote-per-identity
voting, project comments, results hidden until an organizer publishes, and a
per-judge stable ballot order. These assert the rules in the backend, so a
regression is caught before a judge or the acceptance checker ever runs."""
import os
import tempfile
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.test import APITestCase

from core.models import (
    AuditLog,
    Comment,
    Criterion,
    Event,
    JudgeAssignment,
    Project,
    Role,
    Rubric,
    Score,
    ScoreValue,
    Team,
    Track,
    User,
    Vote,
)

_KEYDIR = tempfile.mkdtemp(prefix="dogfood-community-keys-")
_KEY_PATH = os.path.join(_KEYDIR, "signing_key.pem")


def _make_user(username, role, external_id=None):
    user = User.objects.create(username=username, role=role, external_id=external_id)
    return user, Token.objects.create(user=user).key


def _make_event(published=False):
    return Event.objects.create(
        external_id="evt_01",
        name="Sample Hack",
        submissions_close=datetime(2026, 3, 1, 18, 0, tzinfo=dt_timezone.utc),
        results_published=published,
    )


def _make_project(event, track, team, pid, title, repo="https://x/r", **extra):
    return Project.objects.create(
        external_id=pid, team=team, track=track, title=title,
        repo_url=repo, status=Project.Status.SUBMITTED, **extra,
    )


class VoteTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        event = _make_event()
        track = Track.objects.create(external_id="trk_01", event=event, name="Tools")
        team = Team.objects.create(external_id="tm_01", event=event, name="A")
        cls.p1 = _make_project(event, track, team, "prj_01", "Glass Signal", "https://x/r1")
        cls.p2 = _make_project(event, track, team, "prj_02", "Small Meadow", "https://x/r2")
        # a duplicate and a draft, neither votable
        _make_project(
            event, track, team, "prj_dup", "Glass Signal", "https://x/r1",
            is_duplicate=True, duplicate_of=cls.p1,
        )
        Project.objects.create(
            external_id="prj_draft", team=team, track=track, title="Draft",
            status=Project.Status.DRAFT,
        )
        cls.participant, cls.tok_p = _make_user("priya@example.org", Role.PARTICIPANT)
        cls.other, cls.tok_o2 = _make_user("wei@example.org", Role.PARTICIPANT)

    def setUp(self):
        cache.clear()

    def test_anonymous_needs_voter_ref(self):
        resp = self.client.post("/api/projects/prj_01/vote", {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Vote.objects.filter(project=self.p1).exists())

    def test_anonymous_one_vote_per_ref(self):
        first = self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "device-7"}, format="json"
        )
        self.assertEqual(first.status_code, 201)
        again = self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "device-7"}, format="json"
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(Vote.objects.filter(project=self.p1).count(), 1)

    def test_authenticated_one_vote_per_account_ignores_ref(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok_p}")
        first = self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "ref-a"}, format="json"
        )
        self.assertEqual(first.status_code, 201)
        # a different ref must not buy a second vote: identity is the account
        again = self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "ref-b"}, format="json"
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(
            Vote.objects.filter(
                project=self.p1, voter_ref=f"user:{self.participant.pk}"
            ).count(),
            1,
        )

    def test_two_accounts_each_vote_once(self):
        for tok in (self.tok_p, self.tok_o2):
            self.client.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
            resp = self.client.post("/api/projects/prj_01/vote", {}, format="json")
            self.assertEqual(resp.status_code, 201)
        self.assertEqual(Vote.objects.filter(project=self.p1).count(), 2)

    def test_vote_on_missing_or_hidden_project_404(self):
        for pid in ("prj_nope", "prj_dup", "prj_draft"):
            resp = self.client.post(
                f"/api/projects/{pid}/vote", {"voter_ref": "x"}, format="json"
            )
            self.assertEqual(resp.status_code, 404, pid)

    def test_vote_is_audited(self):
        self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "device-9"}, format="json"
        )
        self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "device-9"}, format="json"
        )
        self.assertEqual(
            AuditLog.objects.filter(action="vote.cast", target="prj_01").count(), 1
        )
        self.assertEqual(
            AuditLog.objects.filter(action="vote.duplicate", target="prj_01").count(), 1
        )


class CommentTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        event = _make_event()
        track = Track.objects.create(external_id="trk_01", event=event, name="Tools")
        team = Team.objects.create(external_id="tm_01", event=event, name="A")
        cls.p1 = _make_project(event, track, team, "prj_01", "Glass Signal", "https://x/r1")
        cls.user, cls.tok = _make_user("ada@example.org", Role.PARTICIPANT)

    def setUp(self):
        cache.clear()

    def test_list_is_public(self):
        resp = self.client.get("/api/projects/prj_01/comments")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 0)

    def test_anonymous_cannot_post(self):
        resp = self.client.post(
            "/api/projects/prj_01/comments", {"body": "nice"}, format="json"
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(Comment.objects.count(), 0)

    def test_authenticated_can_post_and_it_lists(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok}")
        resp = self.client.post(
            "/api/projects/prj_01/comments", {"body": "great work"}, format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.client.credentials()
        listing = self.client.get("/api/projects/prj_01/comments").json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["comments"][0]["body"], "great work")
        self.assertEqual(listing["comments"][0]["author"], "ada@example.org")

    def test_empty_body_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok}")
        resp = self.client.post(
            "/api/projects/prj_01/comments", {"body": "   "}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_comment_on_missing_project_404(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok}")
        resp = self.client.post(
            "/api/projects/prj_nope/comments", {"body": "x"}, format="json"
        )
        self.assertEqual(resp.status_code, 404)

    def test_comment_is_audited(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok}")
        self.client.post(
            "/api/projects/prj_01/comments", {"body": "audited"}, format="json"
        )
        self.assertEqual(
            AuditLog.objects.filter(action="comment.create", target="prj_01").count(), 1
        )


@override_settings(SIGNING_KEY_PATH=_KEY_PATH, GIT_COMMIT="test-commit")
class HiddenResultsTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        event = _make_event(published=False)
        track = Track.objects.create(external_id="trk_01", event=event, name="Tools")
        rubric = Rubric.objects.create(event=event, name="Weighted")
        crit = {}
        for key, name, weight, order in [
            ("functionality", "Functionality", "0.4", 0),
            ("quality", "Quality", "0.4", 1),
            ("innovation", "Innovation", "0.2", 2),
        ]:
            crit[key] = Criterion.objects.create(
                rubric=rubric, key=key, name=name, weight=weight, order=order
            )
        teams = {
            t: Team.objects.create(external_id=t, event=event, name=t)
            for t in ("tm_a", "tm_b")
        }
        p1 = _make_project(event, track, teams["tm_a"], "prj_01", "Alpha", "https://x/r1")
        p2 = _make_project(event, track, teams["tm_b"], "prj_02", "Beta", "https://x/r2")
        jdg1 = User.objects.create(username="jdg_01", role=Role.JUDGE, external_id="jdg_01")
        jdg2 = User.objects.create(username="jdg_02", role=Role.JUDGE, external_id="jdg_02")

        def score(judge, project, vals):
            s = Score.objects.create(judge=judge, project=project)
            for key, value in vals.items():
                ScoreValue.objects.create(score=s, criterion=crit[key], value=value)

        score(jdg1, p1, {"functionality": 5, "quality": 5, "innovation": 4})
        score(jdg2, p1, {"functionality": 4, "quality": 5, "innovation": 5})
        score(jdg1, p2, {"functionality": 2, "quality": 3, "innovation": 2})
        score(jdg2, p2, {"functionality": 3, "quality": 2, "innovation": 3})

        cls.organizer, cls.tok_o = _make_user("organizer", Role.ORGANIZER)
        cls.tok_j = Token.objects.create(user=jdg1).key
        cls.participant, cls.tok_p = _make_user("priya@example.org", Role.PARTICIPANT)

    def setUp(self):
        cache.clear()

    def auth(self, key):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {key}")

    def test_results_hidden_before_publish(self):
        self.client.credentials()
        anon = self.client.get("/api/results")
        self.assertEqual(anon.status_code, 200)
        self.assertFalse(anon.json()["published"])
        self.assertEqual(anon.json()["ranking"], [])
        self.assertEqual(self.client.get("/api/results/bundle").status_code, 404)
        # a judge is a non-organizer for results visibility
        self.auth(self.tok_j)
        self.assertFalse(self.client.get("/api/results").json()["published"])
        self.assertEqual(self.client.get("/api/results/bundle").status_code, 404)
        # so is a participant
        self.auth(self.tok_p)
        self.assertEqual(self.client.get("/api/results").json()["ranking"], [])

    def test_organizer_bundle_is_404_until_published(self):
        self.auth(self.tok_o)
        self.assertEqual(self.client.get("/api/results/bundle").status_code, 404)

    def test_publish_reveals_results_to_everyone(self):
        self.auth(self.tok_o)
        pub = self.client.post("/api/results/publish", {}, format="json")
        self.assertEqual(pub.status_code, 201, pub.content)
        self.assertTrue(Event.objects.get(external_id="evt_01").results_published)
        self.client.credentials()
        ranking = self.client.get("/api/results").json()
        self.assertTrue(ranking["published"])
        ranks = {e["project"]: e["rank"] for e in ranking["ranking"]}
        self.assertLess(ranks["prj_01"], ranks["prj_02"])
        self.assertEqual(self.client.get("/api/results/bundle").status_code, 200)
        self.auth(self.tok_j)
        self.assertEqual(self.client.get("/api/results/bundle").status_code, 200)


class BallotOrderTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        event = _make_event()
        track = Track.objects.create(external_id="trk_01", event=event, name="Tools")
        team = Team.objects.create(external_id="tm_01", event=event, name="A")
        cls.pids = [f"prj_{i:02d}" for i in range(1, 7)]  # six projects
        projects = {
            pid: _make_project(event, track, team, pid, pid.upper(), f"https://x/{pid}")
            for pid in cls.pids
        }
        cls.judge_x, cls.tok_x = _make_user("jdg_x", Role.JUDGE, "jdg_x")
        cls.judge_y, cls.tok_y = _make_user("jdg_y", Role.JUDGE, "jdg_y")
        for pid in cls.pids:
            JudgeAssignment.objects.create(judge=cls.judge_x, project=projects[pid])
            JudgeAssignment.objects.create(judge=cls.judge_y, project=projects[pid])
        cls.participant, cls.tok_p = _make_user("priya@example.org", Role.PARTICIPANT)

    def setUp(self):
        cache.clear()

    def order(self, tok):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
        resp = self.client.get("/api/judge/ballot")
        self.assertEqual(resp.status_code, 200)
        return [row["project"] for row in resp.json()["ballot"]]

    def test_ballot_is_stable_across_requests(self):
        self.assertEqual(self.order(self.tok_x), self.order(self.tok_x))

    def test_ballot_is_a_permutation_of_assignments(self):
        self.assertEqual(sorted(self.order(self.tok_x)), self.pids)

    def test_ballot_order_differs_per_judge(self):
        self.assertNotEqual(self.order(self.tok_x), self.order(self.tok_y))

    def test_ballot_is_shuffled_not_sorted(self):
        # at least one judge's order is not the naive id sort, proving a shuffle
        self.assertTrue(
            self.order(self.tok_x) != self.pids or self.order(self.tok_y) != self.pids
        )

    def test_participant_cannot_read_ballot(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok_p}")
        self.assertEqual(self.client.get("/api/judge/ballot").status_code, 403)

    def test_ballot_holds_only_own_assignments(self):
        # a judge with no assignments gets an empty ballot, never a peer's
        _, tok_z = _make_user("jdg_z", Role.JUDGE, "jdg_z")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {tok_z}")
        self.assertEqual(self.client.get("/api/judge/ballot").json()["count"], 0)


class RateLimitTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        event = _make_event()
        track = Track.objects.create(external_id="trk_01", event=event, name="Tools")
        team = Team.objects.create(external_id="tm_01", event=event, name="A")
        _make_project(event, track, team, "prj_01", "Alpha", "https://x/r1")
        _make_project(event, track, team, "prj_02", "Beta", "https://x/r2")
        cls.user, cls.tok = _make_user("ada@example.org", Role.PARTICIPANT)

    def setUp(self):
        cache.clear()
        # Drop the write limits to one per minute for the length of the test.
        # Patch the live rates dict in place so the change is seen whether or
        # not DRF cached the settings object.
        patch = mock.patch.dict(
            ScopedRateThrottle.THROTTLE_RATES, {"vote": "1/min", "comment": "1/min"}
        )
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(cache.clear)

    def test_vote_write_is_rate_limited(self):
        first = self.client.post(
            "/api/projects/prj_01/vote", {"voter_ref": "a"}, format="json"
        )
        self.assertEqual(first.status_code, 201)
        # a second vote within the window is throttled before the view runs,
        # even though it targets a different project
        second = self.client.post(
            "/api/projects/prj_02/vote", {"voter_ref": "b"}, format="json"
        )
        self.assertEqual(second.status_code, 429)

    def test_comment_write_is_rate_limited(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.tok}")
        first = self.client.post(
            "/api/projects/prj_01/comments", {"body": "one"}, format="json"
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            "/api/projects/prj_02/comments", {"body": "two"}, format="json"
        )
        self.assertEqual(second.status_code, 429)

    def test_comment_list_is_not_throttled(self):
        # GET carries no write throttle, so repeated reads never hit 429
        for _ in range(3):
            self.assertEqual(
                self.client.get("/api/projects/prj_01/comments").status_code, 200
            )




