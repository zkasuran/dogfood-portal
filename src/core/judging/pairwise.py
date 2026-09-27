#!/usr/bin/env python3
"""Bradley-Terry pairwise ranking (the Pairwise Mode bonus).

We have rubric scores, not pairwise votes, so we synthesize comparisons WITHIN each
judge: if a judge scored project A above project B, that is one win for A over B.
Comparing within a judge cancels that judge's leniency and scale, which is why pairwise
needs no per-judge normalization. Ties are dropped, so a judge who scored everyone the
same contributes nothing, correctly. Strengths are fit by the standard MM iteration with
a virtual opponent added for every project (the regularizer from the Crowd-BT paper) so
the estimate is unique even when the comparison graph is sparse or disconnected. Each
strength is reported with a bootstrap standard error, so a reader sees which ranks are
solid and which lean on a handful of comparisons. Sources in
work/dogfood/.hq/research/judging-engine.md section 3.
"""
import math
import random
from collections import defaultdict
from itertools import combinations

import engine


def build_pairs(records):
    """Return (wins, projects) where wins[(winner, loser)] counts within-judge orderings."""
    by_judge = defaultdict(list)
    for j, p, x in records:
        by_judge[j].append((p, x))
    wins = defaultdict(int)
    projects = set()
    for lst in by_judge.values():
        for (pa, xa), (pb, xb) in combinations(lst, 2):
            projects.add(pa)
            projects.add(pb)
            if xa > xb:
                wins[(pa, pb)] += 1
            elif xb > xa:
                wins[(pb, pa)] += 1
    return wins, projects


def _mm_fit(wins, projects, iters=2000, tol=1e-10):
    """The MM iteration with a virtual opponent (strength 1) that every project both beats
    once and loses to once. That regularizer pins the scale and keeps the maximum
    likelihood estimate unique on a sparse or disconnected graph. Returns pi per project."""
    projects = sorted(projects)
    W = defaultdict(float)
    ncmp = defaultdict(lambda: defaultdict(int))
    opp = defaultdict(set)
    for (i, k), c in wins.items():
        W[i] += c
        ncmp[i][k] += c
        ncmp[k][i] += c
        opp[i].add(k)
        opp[k].add(i)
    pi = {p: 1.0 for p in projects}
    pi_o = 1.0
    for _ in range(iters):
        new = {}
        for i in projects:
            denom = sum(ncmp[i][k] / (pi[i] + pi[k]) for k in opp[i])
            denom += 2.0 / (pi[i] + pi_o)  # one virtual win plus one virtual loss
            new[i] = (W[i] + 1.0) / denom if denom > 0 else pi[i]
        maxrel = max((abs(new[i] - pi[i]) / (pi[i] or 1.0) for i in projects), default=0.0)
        pi = new
        if maxrel < tol:
            break
    return pi


def fit(records, iters=2000, tol=1e-10):
    """Fit strengths, one log-strength per project. Deterministic."""
    wins, projects = build_pairs(records)
    pi = _mm_fit(wins, projects, iters, tol)
    strength = {p: math.log(pi[p]) for p in projects}
    return {"method": "bradley-terry-mm-virtual", "pi": pi, "strength": strength,
            "comparisons": sum(wins.values()), "projects": len(pi)}


def bootstrap_se(records, draws=300, seed=0):
    """Standard error of each project's log-strength, by resampling the synthesized
    comparisons with replacement and refitting. Deterministic given the seed, so a bundle
    that reports these numbers stays reproducible. Returns {project: se}."""
    wins, projects = build_pairs(records)
    projects = sorted(projects)
    pool = []
    for (i, k), c in wins.items():
        pool.extend([(i, k)] * c)
    if not pool:
        return {p: 0.0 for p in projects}
    rng = random.Random(seed)
    samples = {p: [] for p in projects}
    n = len(pool)
    for _ in range(draws):
        boot = defaultdict(int)
        for _ in range(n):
            i, k = pool[rng.randrange(n)]
            boot[(i, k)] += 1
        pi = _mm_fit(boot, projects)
        for p in projects:
            samples[p].append(math.log(pi[p]))
    out = {}
    for p in projects:
        xs = samples[p]
        mean = sum(xs) / len(xs)
        out[p] = math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))
    return out


def ranking(fit_out):
    s = fit_out["strength"]
    return sorted(s, key=lambda p: (-s[p], p))


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "/home/asuran/Downloads/hackathon-hq/work/dogfood/spec/fixtures.json"
    fixture = engine.load_fixture(path)
    records, _ = engine.build_records(fixture)
    bt = fit(records)
    se = bootstrap_se(records, draws=150)
    add = engine.additive_model(records)
    bt_order = ranking(bt)
    add_order = engine.ranking(add)
    print(f"pairwise comparisons synthesized: {bt['comparisons']} over {bt['projects']} projects")
    top = bt_order[0]
    print(f"BT winner: {top}  strength {bt['strength'][top]:.4f} +/- {se[top]:.4f}")
    print("BT top 5:      ", bt_order[:5])
    print("additive top 5:", add_order[:5])
    overlap = len(set(bt_order[:10]) & set(add_order[:10]))
    print(f"top-10 overlap BT vs additive: {overlap}/10")
