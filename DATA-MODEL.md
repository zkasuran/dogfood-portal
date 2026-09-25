# Data model

The fixture file is input, not our schema. We load it, resolve its awkward cases and store
it in a relational shape we can defend. The model is normalized, every score traces back to a
judge, a project and a criterion. The whole event round-trips out to JSON and back in.
SQLite in development, Postgres-compatible, one migration set.

## Entities

`User`
- `role` in {participant, judge, organizer, admin}, `external_id`, `display_name`
- `judge_tracks` many-to-many to `Track`. A judge is scoped to the tracks they may see, which
  is the backend fact role isolation is enforced against, not a UI flag.

`Event`
- `external_id`, `name`, `submissions_close`, `starts_at`, `ends_at`. Deadlines are stored, so
  a closed event refuses a submission at the model layer rather than in the template.

`Prize` belongs to `Event`: `place`, `amount`, `label`. Prizes are configurable per event.

`Track` belongs to `Event`: `external_id`, `name`.

`Team` belongs to `Event`: `external_id`, `name`. `Membership(team, user)` joins members to a
team, so team size and roster are queries, not columns.

`Project` (a submission) belongs to `Team` and `Track`:
- content: `title`, `summary`, `description`, `repo_url`, `demo_video_url`, `live_url`, `tech_tags`
- lifecycle: `status` in {draft, submitted}, `submitted_at`
- duplicates: `is_duplicate` and `duplicate_of` point a duplicate at its canonical project, so
  the fixture's repeated submission is kept for the record but scored once.

`JudgeAssignment(judge, project, batch)` records who reviews what, in which batch. Coverage
gaps and unfinished batches are read off this table against the scores that exist.

`Rubric(event, name)` and `Criterion(rubric, key, name, weight, order)`:
- weights are `Decimal` and live on the criterion, per event. This is the weighted rubric the
  market leader cannot express. Weights are versioned by rubric, never edited in place under a
  score.

`Score(judge, project, comment, created_at)` and `ScoreValue(score, criterion, value)`:
- one `Score` per judge per project, with one `ScoreValue` per criterion (integer 1 to 5). The
  split keeps per-criterion detail for normalization and export rather than collapsing to a total
  too early.

`Vote(project, ...)` records community votes with one vote per verified identity and an audit
column, so popular voting has integrity rather than a raw tally.

`AuditLog` is append-only: actor, action, target, timestamp. Every merge, assignment, publish,
role-isolation denial and flagged judge is recorded here.

`Bundle` stores each published signed results bundle (payload, digest, public key, signature),
so a result is reproducible and checkable after the fact. See JUDGING.md and verify.py.

## Relationships

Event is the root. Track, Team, Prize and Rubric hang off Event. A Project belongs to one Team
and one Track. A Score belongs to one Judge (User) and one Project and fans out to ScoreValues,
one per Criterion. JudgeAssignment links judges to projects. A duplicate Project points at its
canonical sibling through `duplicate_of`.

## Invariants the schema enforces

- A submission belongs to exactly one team and one track.
- A judge scores a project at most once (unique on judge plus project).
- A ScoreValue references a criterion that belongs to the same event's rubric, values 1 to 5.
- A duplicate project has `is_duplicate` true and a non-null `duplicate_of` and is excluded
  from ranking while its scores merge into the canonical project.
- Rubric weights are read from the rubric version in force when a score was cast, never patched.

## How judging reads the model

The judging engine works on `(judge_id, canonical_project_id, composite)` triples. An adapter
walks Scores and their ScoreValues, applies the criterion weights with `engine.composite`, maps
any duplicate project to its `duplicate_of` and hands the triples to `engine.additive_model`.
The engine never touches the ORM, so it stays pure and testable. See JUDGING.md.

## Import and export (the migration path in and out)

- Import: the seed loads `fixtures.json` on first boot. The same importer accepts a CSV or JSON
  export from another platform, mapped onto the entities above, so an organizer arrives with the
  data they already have.
- Export: every stage exports to CSV (registrants, submissions, scores, results), which is the
  table stake. A full-fidelity JSON dump of every entity exports the whole event. Re-importing
  it into a fresh instance reproduces the same state. That round-trip is the portability proof. It
  answers the walled-garden problem every incumbent leaves in place.
