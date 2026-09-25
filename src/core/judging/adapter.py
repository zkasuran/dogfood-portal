"""Bridge the Django ORM to the pure judging engine.

The engine works on `(judge_id, canonical_project_id, composite)` triples and never
imports Django. This module reads live Score, ScoreValue, Criterion and Rubric rows,
maps a duplicate project onto its canonical sibling through `is_duplicate` and
`duplicate_of`, applies the rubric's weights with `engine.composite`, and produces
the records the engine expects. It also builds the fixture-shaped source that
`bundle.build_payload` consumes, so a published bundle is built from the app's live
data rather than from fixtures.json.

Weights are stored as Decimal on the Criterion. The engine now casts them to float,
but the signed bundle serializes its weights to canonical JSON, which cannot encode a
Decimal, so this adapter converts weights to float before they ever reach the payload.
"""
import engine  # top-level; resolved by core.judging.__init__ path setup

from core.models import Criterion, Project, Score


def rubric_weights(rubric=None):
    """Return {criterion_key: float_weight}. Scoped to one rubric when given, else
    every criterion. Float, not Decimal, so the weights survive canonical JSON."""
    qs = Criterion.objects.all()
    if rubric is not None:
        qs = qs.filter(rubric=rubric)
    return {c.key: float(c.weight) for c in qs.order_by("order", "id")}


def _iter_scores():
    """Yield (judge_id, canonical_project_id, criteria_dict) for every score, with a
    duplicate project's scores counted under its canonical sibling."""
    scores = (
        Score.objects.select_related("judge", "project", "project__duplicate_of")
        .prefetch_related("values__criterion")
        .order_by("judge__external_id", "project__external_id")
    )
    for s in scores:
        proj = s.project
        if proj.is_duplicate and proj.duplicate_of_id is not None:
            canon = proj.duplicate_of.external_id
        else:
            canon = proj.external_id
        criteria = {v.criterion.key: v.value for v in s.values.all()}
        judge_id = s.judge.external_id or s.judge.username
        yield judge_id, canon, criteria


def build_records(weights=None):
    """The (judge_id, canonical_project_id, composite) triples the engine expects,
    with the rubric weights applied by engine.composite and duplicates merged."""
    weights = weights or rubric_weights()
    return [
        (judge_id, canon, engine.composite(criteria, weights))
        for judge_id, canon, criteria in _iter_scores()
    ]


def build_source():
    """A fixture-shaped dict {projects, scores} from live ORM rows, for
    bundle.build_payload. Scores are pre-mapped to their canonical project id and
    duplicate projects are dropped from the project list, so the engine's own dedupe
    is a consistent no-op and the ORM's is_duplicate/duplicate_of stays authoritative.
    """
    scores = [
        {"judge": judge_id, "project": canon, "criteria": criteria}
        for judge_id, canon, criteria in _iter_scores()
    ]
    projects = [
        {
            "id": p.external_id,
            "team": p.team.external_id,
            "title": p.title,
            "repo_url": p.repo_url,
        }
        for p in (
            Project.objects.select_related("team")
            .filter(is_duplicate=False)
            .order_by("external_id")
        )
    ]
    return {"projects": projects, "scores": scores}
