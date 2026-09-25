"""OpenAPI schema helpers for drf-spectacular.

The URL config registers both the bare route and its trailing-slash twin
(/api/judge/scores and /api/judge/scores/) so a client is never turned away
over one slash. That leniency belongs in routing, not in the published
schema, where a doubled path reads as noise and collides operation ids. The
preprocessing hook below keeps one entry per route.
"""


def dedupe_trailing_slash(endpoints, **kwargs):
    """Drop the trailing-slash variant of any route whose slash-free sibling
    is also registered. Runs before operation ids are assigned, so the schema
    carries each endpoint once."""
    paths = {path for path, _regex, _method, _callback in endpoints}
    kept = []
    for endpoint in endpoints:
        path = endpoint[0]
        if path.endswith("/") and len(path) > 1 and path.rstrip("/") in paths:
            continue
        kept.append(endpoint)
    return kept
