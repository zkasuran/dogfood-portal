"""Tests for the pairwise engine's bootstrap standard errors.

These run without the database: they call the pure Bradley-Terry functions on a small
hand-built set of within-judge orderings, so they check the maths rather than a request.
"""
import unittest

from core.judging import pairwise

# (judge, project, composite). j2 scores p1 and p2 the same, so that pair is a tie and
# contributes no comparison. Across the three judges p3 loses every comparison, so it
# must rank last. p1 versus p2 is one win each.
RECORDS = [
    ("j1", "p1", 5.0), ("j1", "p2", 3.0), ("j1", "p3", 1.0),
    ("j2", "p1", 4.0), ("j2", "p2", 4.0), ("j2", "p3", 2.0),
    ("j3", "p1", 4.0), ("j3", "p2", 5.0), ("j3", "p3", 3.0),
]


class PairwiseStandardErrorTests(unittest.TestCase):
    def test_se_is_nonnegative_and_covers_every_ranked_project(self):
        fit = pairwise.fit(RECORDS)
        se = pairwise.bootstrap_se(RECORDS, draws=60, seed=0)
        self.assertEqual(set(se), set(fit["strength"]))
        self.assertTrue(all(v >= 0.0 for v in se.values()))

    def test_se_is_deterministic_for_a_fixed_seed(self):
        a = pairwise.bootstrap_se(RECORDS, draws=60, seed=0)
        b = pairwise.bootstrap_se(RECORDS, draws=60, seed=0)
        self.assertEqual(a, b)

    def test_ties_drop_and_the_loser_ranks_last(self):
        fit = pairwise.fit(RECORDS)
        order = pairwise.ranking(fit)
        self.assertEqual(order[-1], "p3")
        self.assertEqual(fit["projects"], 3)
