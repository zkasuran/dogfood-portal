# DOGFOOD portal

An open source, self hostable hackathon submission and judging portal. Teams
submit projects to a public gallery, judges score them against a weighted rubric
and organizers publish results. It boots offline in one container with the shared
fixture data already loaded.

The differentiator is Reproducible Signed Results. When an organizer publishes,
the portal emits a signed bundle carrying the raw scores, the rubric with its
weights, the scoring method with its parameters plus the code commit that produced
them. Anyone recomputes the ranking from that bundle and checks the signature with
a standalone `verify.py`, so a winner is provable without trusting the host. See
JUDGING.md.

Built for DOGFOOD 2026 on Django, Django REST Framework and SQLite.

## Demo

A walkthrough of one full lifecycle (create, submit, judge, publish), ending with the
verifier passing on the real signed bundle then rejecting a tampered one:
https://youtu.be/igjISVTL2ik

A five minute recording of that lifecycle is committed as
[`demo/dogfood-demo.mp4`](demo/dogfood-demo.mp4). It is recorded, not edited:
`demo/record_demo.py` boots a fresh seeded portal, drives the real UI in a headless
browser, runs every terminal command it shows, then encodes the MP4. To regenerate:

```bash
pip install -r requirements.txt playwright imageio-ffmpeg && playwright install chromium
python3 demo/record_demo.py
```

## Run it

One command brings up a seeded, working portal with the network off:

```bash
docker compose up
```

That builds one image and starts one container. On boot it migrates the database,
loads `spec/fixtures.json`, generates a signing key on first boot then serves the
portal at http://localhost:8080. There is no cloud account, no hosted database, no
external API.

Open http://localhost:8080 for the gallery, http://localhost:8080/admin/ for the
organizer console (seed login `organizer` / `organizer`) or
http://localhost:8080/api/docs for the OpenAPI browser.

If port 8080 is taken, publish on another host port:

```bash
DOGFOOD_PORT=9000 docker compose up
```

The portal still listens on 8080 inside the container. Update `base_url` in
`.dogfood.toml` to match if you run the checker against the new port.

## The seed tokens

The checker never logs in, so the seed creates one account per role with a fixed
token and prints the four header lines on boot:

```
organizer   = "Authorization: Token organizer0000000000000000000000000000000"
judge_a     = "Authorization: Token judgea0000000000000000000000000000000000"
judge_b     = "Authorization: Token judgeb0000000000000000000000000000000000"
participant = "Authorization: Token participant00000000000000000000000000000"
```

They are already copied into `.dogfood.toml [auth]`. These four tokens are dev and
seed only. They exist so an offline checker can attach a per-role header without a
login step. Do not carry them into a real deployment, which issues normal tokens.

## Run the acceptance checker

```bash
python3 spec/run.py .dogfood.toml > acceptance-report.txt
```

The checker is standard library Python with nothing to install. It reads
`fixtures.json` beside `run.py`, attaches a per-role token from `.dogfood.toml`
then makes seven HTTP requests. The committed `acceptance-report.txt` shows 7 of 7
PASS at T1 and T2.

## Roles and isolation

Every account has one role: visitor, participant, judge, organizer or admin. The
role is the security boundary, enforced by DRF permission classes in
`src/core/permissions.py` rather than in a template. A curl as the wrong role is
refused before any view code runs.

- The gallery is public.
- Submitting after the deadline is refused with 403. The fixture event closed on
  2026-03-01, so the seeded portal is already closed.
- `GET /api/judge/scores` returns the caller's own scores. A judge sees only their
  own. An organizer or admin may read another judge's scores with `?judge_id=<id>`.
  A judge asking for a peer by id is refused with 403. That is the check most
  portals fail, so it lives in the backend.
- `GET /api/export/results.csv` is organizer only. A judge or a participant is
  refused with 403.

For example, judge_b asking for judge_a (fixture id `jdg_07`) is turned away:

```bash
curl -H "Authorization: Token judgeb0000000000000000000000000000000000" \
  "http://localhost:8080/api/judge/scores?judge_id=jdg_07"
# HTTP 403 Forbidden
```

## How judging works

Reviews become a ranking that stays fair when judges see different projects, when
some projects get more reviews than others and when a judge scores everyone the
same. Criteria are weighted into a composite, judge bias is removed with an
additive model then each project's quality is shrunk toward the mean by its review
count, so coverage buys confidence rather than rank. A guarded z-score runs as a
cross-check. JUDGING.md carries the math, the awkward cases and the references.

## Reproducible signed results

On publish the portal builds a bundle of the raw scores, the rubric with its
weights, the method with its fitted parameters plus the code commit, hashes it with
sha256 over a canonical encoding then signs it with an Ed25519 key. The key is
generated by the deployment on first boot and kept in the deployment's own volume.
It is never a personal or shared key, because the product is meant to be forked and
self hosted. The public key is served at `/api/verification-key` and travels inside
every bundle.

Publish requires an organizer token and at least one score. On the seeded portal,
publish once, read the bundle then verify it:

```bash
curl -X POST -H "Authorization: Token organizer0000000000000000000000000000000" \
  http://localhost:8080/api/results/publish
curl http://localhost:8080/api/results/bundle > bundle.json
python3 verify.py bundle.json
```

`verify.py` recomputes the ranking from the raw scores, confirms it matches the
published ranking, checks the digest then verifies the signature against the public
key in the bundle. It needs only the standard library plus `cryptography` for the
signature step. Tampering with a score or a rank fails the check.

## API

The API is documented at `/api/docs` (OpenAPI via drf-spectacular). The main routes:

| Name | Path | Who |
| --- | --- | --- |
| gallery | `GET /projects` | public |
| submit | `POST /api/projects` | participant, refused when closed |
| judge scores | `GET /api/judge/scores` | judge, organizer, admin |
| peer scores | `GET /api/judge/scores?judge_id=<id>` | own judge or organizer/admin |
| csv export | `GET /api/export/results.csv` | organizer, admin |
| publish | `POST /api/results/publish` | organizer, admin |
| results bundle | `GET /api/results/bundle` | public |
| verification key | `GET /api/verification-key` | public |
| progress | `GET /api/progress` | organizer, admin |
| health | `GET /health` | public |
| api docs | `GET /api/docs` | public |

## Move an event in or out

An event exports to one JSON file and imports into a fresh database, so an
organizer can carry a whole event between instances:

```bash
python manage.py export_event evt_01 > event.json
python manage.py import_event event.json
```

The round trip preserves every project, team, judge, score and the weighted rubric,
including the flagged duplicate. MIGRATION.md has the exact commands and what
travels.

## Layout

```
src/            Django project (dogfood) and the core app
  core/models.py                              the schema
  core/permissions.py                         role isolation
  core/api_views.py                           judge scores, submit, CSV, publish
  core/judging/                               pure scoring engine, signing, bundle
  core/management/commands/seed.py            loads fixtures, prints tokens
  core/management/commands/export_event.py    dump an event to JSON
  core/management/commands/import_event.py    load an event into a fresh database
spec/           the acceptance checker, fixtures and this program's spec
verify.py       standalone bundle verifier
.dogfood.toml   points the checker at the portal
docker-compose.yml   one command to a seeded, offline portal
```

## License

MIT. See `LICENSE`. The program requires an OSI license, so the portal ships under
MIT to be forked and self hosted freely.
