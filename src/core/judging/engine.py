"""DOGFOOD judging engine: weighted composite plus cross-judge normalization.

Primary method: an additive model x_ij = mu + b_j + q_i + e_ij, where b_j is a
judge's leniency bias and q_i is a project's quality. Projects are ranked by the
empirical-Bayes shrunken quality mu + q_hat_i, so a project reviewed twice is not
beaten by a lucky single high score. Fallback and cross-check: a guarded per-judge
z-score. Rules that make it defensible: weight the criteria first then normalize the
composite, never sum across judges (average then shrink by n/(n+k)), and a judge with
no score variance contributes a bias offset and zero ranking signal with no special
case. See work/dogfood/.hq/research/judging-engine.md for the derivation and sources.

Pure standard library, so the same numbers fall out on any Python 3.
"""
import json
import math
import statistics
from collections import defaultdict

DEFAULT_WEIGHTS = {"functionality": 1.0, "quality": 1.0, "innovation": 1.0}


def load_fixture(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dedupe_projects(projects):
    """Merge duplicate submissions to the lexicographically smallest id.

    A duplicate is two projects sharing a repo_url, or the same team and title.
    Returns a map {project_id: canonical_project_id}.
    """
    canon, by_repo, by_team_title = {}, {}, {}
    for p in sorted(projects, key=lambda p: p["id"]):
        repo = p.get("repo_url")
        tt = (p.get("team"), p.get("title"))
        keeper = by_repo.get(repo) or by_team_title.get(tt)
        if keeper:
            canon[p["id"]] = keeper
        else:
            canon[p["id"]] = p["id"]
            if repo:
                by_repo[repo] = p["id"]
            by_team_title[tt] = p["id"]
    return canon


def composite(criteria, weights):
    """Weighted mean of the criteria present on this score, kept on the 1 to 5 scale.

    Weights may be int, float or Decimal (Django uses a DecimalField for the weight),
    so everything is cast to float here. That keeps the downstream model arithmetic
    all-float and never raises on mixing Decimal with float.
    """
    num = sum(float(weights.get(c, 0.0)) * float(v) for c, v in criteria.items())
    den = sum(float(weights.get(c, 0.0)) for c in criteria)
    return num / den if den else float("nan")


def build_records(fixture, weights=None):
    """Return (records, canon) where records is a list of (judge, project, composite)
    with duplicate projects merged to their canonical id."""
    weights = weights or DEFAULT_WEIGHTS
    canon = dedupe_projects(fixture.get("projects", []))
    records = []
    for s in fixture.get("scores", []):
        proj = canon.get(s["project"], s["project"])
        records.append((s["judge"], proj, composite(s.get("criteria", {}), weights)))
    return records, canon


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def additive_model(records, iters=200, tol=1e-10):
    """Fit x = mu + bias[judge] + quality[project] by coordinate descent, then shrink
    each project's quality toward the mean by n/(n+k). Returns a dict per project with
    n, final score and an uncertainty band, plus the fitted mu, k and judge biases."""
    by_judge, by_proj = defaultdict(list), defaultdict(list)
    for j, p, x in records:
        by_judge[j].append((p, x))
        by_proj[p].append((j, x))
    mu = _mean([x for _, _, x in records])
    bias = dict.fromkeys(by_judge, 0.0)
    quality = dict.fromkeys(by_proj, 0.0)
    for _ in range(iters):
        for j, lst in by_judge.items():
            bias[j] = _mean([x - mu - quality[p] for p, x in lst])
        delta = 0.0
        for p, lst in by_proj.items():
            new = _mean([x - mu - bias[j] for j, x in lst])
            delta = max(delta, abs(new - quality[p]))
            quality[p] = new
        if delta < tol:
            break
    resid = [x - mu - bias[j] - quality[p] for j, p, x in records]
    sigma2 = _mean([r * r for r in resid]) or 1e-9
    proj_effects = list(quality.values())
    mean_n = _mean([len(v) for v in by_proj.values()]) or 1.0
    tau2 = max(statistics.pvariance(proj_effects) - sigma2 / mean_n, 1e-6) if len(proj_effects) > 1 else 1.0
    k = sigma2 / tau2
    out = {}
    for p, lst in by_proj.items():
        n = len(lst)
        raw = _mean([x - mu - bias[j] for j, x in lst])
        shrink = n / (n + k)
        out[p] = {
            "n": n,
            "shrink": shrink,
            "quality": shrink * raw,
            "final": mu + shrink * raw,
            "uncertainty": math.sqrt(tau2 * (1 - shrink)),
        }
    return {"method": "additive-bias-shrinkage", "mu": mu, "k": k,
            "sigma2": sigma2, "tau2": tau2, "bias": bias, "projects": out}


def zscore_fallback(records, min_reviews=5):
    """Guarded per-judge z-score. Mean-center every judge, divide by a spread that is
    floored and that falls back to the global spread for a flat or thin judge, so a
    zero-variance judge is mean-centered and never divided by zero."""
    by_judge = defaultdict(list)
    for j, _, x in records:
        by_judge[j].append(x)
    allx = [x for _, _, x in records]
    g_mean = _mean(allx)
    g_sd = statistics.pstdev(allx) if len(allx) > 1 else 1.0
    within = [statistics.pstdev(xs) for xs in by_judge.values() if len(xs) > 1]
    floor = _mean([w for w in within if w > 0]) or (g_sd / 2 or 0.5)
    m = {j: _mean(xs) for j, xs in by_judge.items()}
    d = {}
    for j, xs in by_judge.items():
        sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        d[j] = g_sd if (sd == 0 or len(xs) < min_reviews) else max(sd, floor)
    zby = defaultdict(list)
    for j, p, x in records:
        zby[p].append((x - m[j]) / d[j])
    out = {p: {"n": len(zs), "final": g_mean + g_sd * _mean(zs)} for p, zs in zby.items()}
    return {"method": "guarded-zscore", "g_mean": g_mean, "g_sd": g_sd,
            "floor": floor, "projects": out}


def ranking(model_out):
    """Project ids ordered best first by final score, ties broken by id for determinism."""
    return sorted(model_out["projects"], key=lambda p: (-model_out["projects"][p]["final"], p))
