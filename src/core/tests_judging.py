"""Integration tests for the judging engine wired into the app.

These cover the ORM-to-records adapter (weighted composite plus duplicate merge), the
organizer-only publish action, the public results bundle and verification key, and the
standalone verify.py passing on a real bundle while failing on a tampered one. The pure
core has its own suite in tests/test_judging.py; this exercises the Django side.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core.judging import adapter
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

# verify.py ships at the repo root as a standalone deliverable. Put the root on the
# path so the in-process round trip uses the same verifier a judge runs by hand.
_ROOT = str(Path(settings.BASE_DIR).parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import verify  # noqa: E402

VERIFY_PY = os.path.join(_ROOT, "verify.py")
_KEYDIR = tempfile.mkdtemp(prefix="dogfood-test-keys-")
_KEY_PATH = os.path.join(_KEYDIR, "signing_key.pem")


def _seed_small_event():
    """A tiny weighted event with one duplicate submission, enough to rank and sign."""
    event = Event.objects.create(
        external_id="evt_t",
        name="Test Hack",
        submissions_close=datetime(2026, 3, 1, 18, 0, tzinfo=dt_timezone.utc),
    )
    track = Track.objects.create(external_id="trk_t", event=event, name="Tools")
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
        t: Team.objects.create(external_id=t, event=event, name=t.upper())
        for t in ("tm_a", "tm_b", "tm_c")
    }
    projects = {}
    for pid, team, repo, title in [
        ("prj_01", "tm_a", "https://x/r1", "Alpha"),
        ("prj_02", "tm_b", "https://x/r2", "Beta"),
        ("prj_03", "tm_c", "https://x/r3", "Gamma"),
    ]:
        projects[pid] = Project.objects.create(
            external_id=pid, team=teams[team], track=track, title=title,
            repo_url=repo, status=Project.Status.SUBMITTED,
        )
    # prj_dup repeats prj_01's team, repo and title, flagged onto its canonical sibling.
    projects["prj_dup"] = Project.objects.create(
        external_id="prj_dup", team=teams["tm_a"], track=track, title="Alpha",
        repo_url="https://x/r1", status=Project.Status.SUBMITTED,
        is_duplicate=True, duplicate_of=projects["prj_01"],
    )

    def judge(uid):
        return User.objects.create(username=uid, role=Role.JUDGE, external_id=uid)

    judges = {u: judge(u) for u in ("jdg_01", "jdg_02", "jdg_03")}

    def score(judge_obj, project, values):
        s = Score.objects.create(judge=judge_obj, project=project)
        for key, val in values.items():
            ScoreValue.objects.create(score=s, criterion=crit[key], value=val)

    score(judges["jdg_01"], projects["prj_01"], {"functionality": 4, "quality": 5, "innovation": 3})
    score(judges["jdg_02"], projects["prj_01"], {"functionality": 5, "quality": 5, "innovation": 5})
    score(judges["jdg_01"], projects["prj_02"], {"functionality": 2, "quality": 2, "innovation": 2})
    score(judges["jdg_02"], projects["prj_02"], {"functionality": 3, "quality": 3, "innovation": 3})
    score(judges["jdg_03"], projects["prj_03"], {"functionality": 4, "quality": 4, "innovation": 4})
    score(judges["jdg_02"], projects["prj_03"], {"functionality": 3, "quality": 4, "innovation": 2})
    # a review on the duplicate, which must count under prj_01
    score(judges["jdg_03"], projects["prj_dup"], {"functionality": 5, "quality": 5, "innovation": 5})
    return rubric


class AdapterTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rubric = _seed_small_event()

    def test_rubric_weights_are_float(self):
        w = adapter.rubric_weights(self.rubric)
        self.assertEqual(w, {"functionality": 0.4, "quality": 0.4, "innovation": 0.2})
        self.assertTrue(all(isinstance(v, float) for v in w.values()))

    def test_records_apply_weights_and_merge_duplicate(self):
        recs = adapter.build_records(adapter.rubric_weights(self.rubric))
        self.assertEqual(len(recs), 7)
        # no record points at the duplicate id
        self.assertTrue(all(p != "prj_dup" for _, p, _ in recs))
        # the duplicate's review counts under prj_01
        self.assertIn(("jdg_03", "prj_01", 5.0), recs)
        # weighted composite: (0.4*4 + 0.4*5 + 0.2*3) / 1.0 = 4.2
        by_key = {(j, p): c for j, p, c in recs}
        self.assertAlmostEqual(by_key[("jdg_01", "prj_01")], 4.2)

    def test_source_drops_duplicate_and_premaps_scores(self):
        source = adapter.build_source()
        ids = {p["id"] for p in source["projects"]}
        self.assertEqual(ids, {"prj_01", "prj_02", "prj_03"})
        self.assertTrue(all(s["project"] != "prj_dup" for s in source["scores"]))
        self.assertTrue(
            any(s["judge"] == "jdg_03" and s["project"] == "prj_01" for s in source["scores"])
        )


@override_settings(SIGNING_KEY_PATH=_KEY_PATH, GIT_COMMIT="test-commit")
class PublishVerifyTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _seed_small_event()
        cls.organizer = User.objects.create(username="org", role=Role.ORGANIZER)
        cls.participant = User.objects.create(username="part", role=Role.PARTICIPANT)
        cls.tok_o = Token.objects.create(user=cls.organizer).key
        cls.tok_j = Token.objects.create(user=User.objects.get(username="jdg_01")).key
        cls.tok_p = Token.objects.create(user=cls.participant).key

    def auth(self, key):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {key}")

    def publish(self):
        self.auth(self.tok_o)
        resp = self.client.post("/api/results/publish", {}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    def test_publish_signs_and_verifies(self):
        signed = self.publish()
        self.assertEqual(signed["payload"]["code_commit"], "test-commit")
        self.assertTrue(all(ok for _, ok in verify.check(signed)))

    def test_bundle_and_key_are_public(self):
        signed = self.publish()
        self.client.credentials()
        bundle_resp = self.client.get("/api/results/bundle")
        self.assertEqual(bundle_resp.status_code, 200)
        self.assertEqual(bundle_resp.json()["digest"], signed["digest"])
        key_resp = self.client.get("/api/verification-key")
        self.assertEqual(key_resp.status_code, 200)
        self.assertEqual(key_resp.json()["public_key"], signed["public_key"])
        self.assertEqual(key_resp.json()["algorithm"], "ed25519")

    def test_publish_is_organizer_only(self):
        self.auth(self.tok_j)
        self.assertEqual(self.client.post("/api/results/publish", {}, format="json").status_code, 403)
        self.auth(self.tok_p)
        self.assertEqual(self.client.post("/api/results/publish", {}, format="json").status_code, 403)
        self.client.credentials()
        self.assertIn(self.client.post("/api/results/publish", {}, format="json").status_code, (401, 403))

    def _run_verify(self, bundle_obj):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(bundle_obj, fh)
            path = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, VERIFY_PY, path], capture_output=True, text=True
            )
            return proc.returncode, proc.stdout
        finally:
            os.unlink(path)

    def test_verify_cli_passes_on_real_bundle(self):
        signed = self.publish()
        rc, out = self._run_verify(signed)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERIFIED", out)

    def test_verify_cli_fails_on_tampered_score(self):
        signed = self.publish()
        signed["payload"]["raw_scores"][0]["criteria"]["quality"] = 1
        rc, out = self._run_verify(signed)
        self.assertEqual(rc, 1, out)

    def test_verify_cli_fails_on_tampered_rank(self):
        signed = self.publish()
        signed["payload"]["ranking"][0]["final"] += 1.0
        rc, out = self._run_verify(signed)
        self.assertEqual(rc, 1, out)
