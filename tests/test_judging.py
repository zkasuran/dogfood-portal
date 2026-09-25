#!/usr/bin/env python3
"""Unit tests for the judging + signing core. Runs under pytest or standalone:

    python3 test_judging.py

Pure standard library plus `cryptography` for the signature tests. Uses a small inline
fixture so the tests do not depend on the event file.
"""
# --- path bootstrap -------------------------------------------------------------
# The core lives in src/core/judging/ and the verifier at the repo root. Put both on
# the path so this suite runs under pytest and standalone (python3 tests/test_judging.py).
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _p in (_os.path.join(_ROOT, "src", "core", "judging"), _ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
# --------------------------------------------------------------------------------

import copy
from decimal import Decimal

import bundle
import canonical
import engine
import pairwise
import signing
import verify

FX = {
    "projects": [
        {"id": "p1", "team": "t1", "track": "tr1", "title": "A", "repo_url": "r1"},
        {"id": "p2", "team": "t2", "track": "tr1", "title": "B", "repo_url": "r2"},
        {"id": "p3", "team": "t1", "track": "tr1", "title": "A", "repo_url": "r1"},
    ],
    "scores": [
        {"judge": "j1", "project": "p1", "criteria": {"functionality": 5, "quality": 5, "innovation": 5}},
        {"judge": "j2", "project": "p1", "criteria": {"functionality": 4, "quality": 4, "innovation": 4}},
        {"judge": "j1", "project": "p2", "criteria": {"functionality": 2, "quality": 2, "innovation": 2}},
        {"judge": "j2", "project": "p2", "criteria": {"functionality": 3, "quality": 3, "innovation": 3}},
        {"judge": "j3", "project": "p3", "criteria": {"functionality": 4, "quality": 4, "innovation": 4}},
        {"judge": "j3", "project": "p2", "criteria": {"functionality": 1, "quality": 1, "innovation": 1}},
        {"judge": "j4", "project": "p1", "criteria": {"functionality": 3, "quality": 3, "innovation": 3}},
        {"judge": "j4", "project": "p2", "criteria": {"functionality": 3, "quality": 3, "innovation": 3}},
    ],
}
W = {"functionality": 1.0, "quality": 1.0, "innovation": 1.0}


def test_composite_equal_weights():
    assert engine.composite({"functionality": 4, "quality": 2}, {"functionality": 1, "quality": 1}) == 3.0


def test_composite_weighted():
    assert engine.composite({"a": 4, "b": 2}, {"a": 3, "b": 1}) == 3.5


def test_composite_decimal_is_float():
    v = engine.composite({"a": 4}, {"a": Decimal("2")})
    assert v == 4.0 and isinstance(v, float)


def test_composite_missing_and_empty():
    assert engine.composite({"a": 4, "b": 2}, {"a": 1}) == 4.0
    nan = engine.composite({}, W)
    assert nan != nan


def test_dedupe_merges_duplicate():
    canon = engine.dedupe_projects(FX["projects"])
    assert canon["p3"] == "p1" and canon["p1"] == "p1"


def test_build_records_maps_duplicate_away():
    recs, _ = engine.build_records(FX, W)
    assert len(recs) == 8
    assert all(p != "p3" for _, p, _ in recs)


def test_additive_ranks_and_bounds():
    recs, _ = engine.build_records(FX, W)
    model = engine.additive_model(recs)
    order = engine.ranking(model)
    assert order[0] == "p1"
    for d in model["projects"].values():
        assert d["final"] == d["final"]
        assert 0.0 < d["shrink"] < 1.0
    assert model["projects"]["p1"]["n"] == 4


def test_zero_variance_judge_handled():
    recs, _ = engine.build_records(FX, W)
    model = engine.additive_model(recs)
    assert "j4" in model["bias"] and abs(model["bias"]["j4"]) < 1e6
    z = engine.zscore_fallback(recs)
    assert all(v["final"] == v["final"] for v in z["projects"].values())


def test_bundle_is_deterministic():
    a = bundle.build_payload(FX, W, code_commit="c")
    b = bundle.build_payload(FX, W, code_commit="c")
    assert canonical.sha256_hex(a) == canonical.sha256_hex(b)


def test_bundle_accepts_decimal_weights():
    import json
    key = signing.load_private_pem(signing.generate_private_pem())
    w = {"functionality": Decimal("0.4"), "quality": Decimal("0.4"), "innovation": Decimal("0.2")}
    signed = bundle.sign_payload(bundle.build_payload(FX, w, code_commit="c"), key)
    json.dumps(signed)  # no Decimal survives into the payload, so this cannot raise
    assert all(isinstance(v, float) for v in signed["payload"]["rubric"]["weights"].values())
    assert all(ok for _, ok in verify.check(signed))


def test_sign_and_verify_roundtrip():
    key = signing.load_private_pem(signing.generate_private_pem())
    signed = bundle.sign_payload(bundle.build_payload(FX, W, code_commit="c"), key)
    assert all(ok for _, ok in verify.check(signed))


def test_signed_lie_about_ranking_fails_reproducibility():
    key = signing.load_private_pem(signing.generate_private_pem())
    signed = bundle.sign_payload(bundle.build_payload(FX, W, code_commit="c"), key)
    lie = copy.deepcopy(signed)
    lie["payload"]["ranking"][0]["final"] += 1.0
    resigned = bundle.sign_payload(lie["payload"], key)
    results = verify.check(resigned)
    assert results[0][1] and results[1][1]      # digest + signature valid on the re-sign
    assert results[2][1] is False               # ranking not reproducible from the raw scores


def test_raw_tamper_breaks_signature():
    key = signing.load_private_pem(signing.generate_private_pem())
    signed = bundle.sign_payload(bundle.build_payload(FX, W, code_commit="c"), key)
    bad = copy.deepcopy(signed)
    bad["payload"]["raw_scores"][0]["criteria"]["quality"] = 1
    assert verify.check(bad)[1][1] is False


def test_pairwise_converges_and_drops_ties():
    recs, _ = engine.build_records(FX, W)
    fit = pairwise.fit(recs)
    order = pairwise.ranking(fit)
    assert order.index("p1") < order.index("p2")
    assert fit["comparisons"] == 3  # j4's all-equal scores contribute no comparisons


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_run())
