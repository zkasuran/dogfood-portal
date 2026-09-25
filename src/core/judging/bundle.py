"""Build and sign a reproducible results bundle.

The payload holds everything needed to recompute the ranking from scratch: the raw
scores (with duplicates already merged to canonical ids), the rubric weights, the
method id, and the code commit that produced it. The ranking itself is included so a
reader sees the result, but a verifier recomputes it from the raw scores and checks
the two agree. The signature is over the canonical bytes of the payload.
"""
import canonical
import engine
import signing

BUNDLE_VERSION = "1"


def build_payload(fixture, weights=None, code_commit="unknown", method="additive"):
    weights = weights or engine.DEFAULT_WEIGHTS
    records, canon = engine.build_records(fixture, weights)
    model = engine.additive_model(records) if method == "additive" else engine.zscore_fallback(records)
    projects = model["projects"]
    order = engine.ranking(model)
    ranking = [
        {
            "rank": i + 1,
            "project": p,
            "final": round(projects[p]["final"], 6),
            "n": projects[p]["n"],
            "uncertainty": round(projects[p].get("uncertainty", 0.0), 6),
        }
        for i, p in enumerate(order)
    ]
    raw = sorted(
        (
            {
                "judge": s["judge"],
                "project": canon.get(s["project"], s["project"]),
                "criteria": s.get("criteria", {}),
            }
            for s in fixture.get("scores", [])
        ),
        key=lambda r: (r["judge"], r["project"]),
    )
    params = {}
    if method == "additive":
        params = {"mu": round(model["mu"], 6), "k": round(model["k"], 6)}
    return {
        "bundle_version": BUNDLE_VERSION,
        "method": model["method"],
        "method_params": params,
        "rubric": {"weights": weights, "version": "v1"},
        "code_commit": code_commit,
        "raw_scores": raw,
        "ranking": ranking,
    }


def sign_payload(payload, private_key):
    """Wrap a payload with its digest, the public key and an Ed25519 signature."""
    message = canonical.canonical_bytes(payload)
    return {
        "payload": payload,
        "digest": canonical.sha256_hex(payload),
        "public_key": signing.public_hex(private_key),
        "signature": signing.sign_hex(private_key, message),
    }
