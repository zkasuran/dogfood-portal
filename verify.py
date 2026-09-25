#!/usr/bin/env python3
"""Standalone verifier for a DOGFOOD reproducible signed results bundle.

It recomputes the ranking from the raw scores in the bundle, checks that it matches
the published ranking, checks the digest over the canonical payload, and verifies the
Ed25519 signature against the public key carried in the bundle. A judge can run this
without trusting the portal and without any key of ours.

    python3 verify.py bundle.json

Dependencies: the standard library, plus `cryptography` for the signature check
(pip install cryptography). Recomputation and the digest are pure standard library.
"""
# --- standalone bootstrap -------------------------------------------------------
# The judging core lives in src/core/judging/. Put it on the path so this verifier
# runs from the repo root with `python3 verify.py bundle.json` and finds engine,
# canonical and signing. This is wiring only; the verification logic below is the
# core's own, unchanged.
import os as _os
import sys as _sys

_sys.path.insert(
    0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "src", "core", "judging")
)
# --------------------------------------------------------------------------------
import json
import sys

import canonical
import engine
import signing


def recompute_ranking(payload):
    weights = payload["rubric"]["weights"]
    records = [
        (r["judge"], r["project"], engine.composite(r["criteria"], weights))
        for r in payload["raw_scores"]
    ]
    method = payload["method"]
    model = engine.additive_model(records) if method.startswith("additive") else engine.zscore_fallback(records)
    projects = model["projects"]
    order = engine.ranking(model)
    return [
        {
            "rank": i + 1,
            "project": p,
            "final": round(projects[p]["final"], 6),
            "n": projects[p]["n"],
            "uncertainty": round(projects[p].get("uncertainty", 0.0), 6),
        }
        for i, p in enumerate(order)
    ]


def check(bundle):
    """Return a list of (label, passed) for every verification step."""
    payload = bundle["payload"]
    results = []
    results.append(("digest matches canonical payload",
                    canonical.sha256_hex(payload) == bundle.get("digest")))
    results.append(("signature valid for the published public key",
                    signing.verify_hex(bundle.get("public_key", ""),
                                       canonical.canonical_bytes(payload),
                                       bundle.get("signature", ""))))
    results.append(("ranking reproducible from the raw scores",
                    recompute_ranking(payload) == payload.get("ranking")))
    return results


def main(argv):
    if len(argv) != 2:
        print("usage: python3 verify.py bundle.json")
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        bundle = json.load(fh)
    results = check(bundle)
    ok = True
    for label, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    ranking = bundle["payload"].get("ranking") or []
    winner = ranking[0]["project"] if ranking else "none"
    print(f"\n{'VERIFIED' if ok else 'NOT VERIFIED'}: winner = {winner}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
