# DOGFOOD portal

An open source, self hostable hackathon submission and judging portal. It
boots offline in one container, seeds itself with the shared fixture data,
and enforces judge role isolation in the API rather than in the templates.

Built for DOGFOOD 2026 on Django, Django REST Framework and SQLite.

## Quick start

```bash
docker compose up
```

That builds one image and starts one container. On boot it migrates the
database, collects static files, loads `spec/fixtures.json` and serves the
portal at http://localhost:8080. No cloud account, no hosted database, no
external API. It runs with the network off.

Open http://localhost:8080 for the gallery, http://localhost:8080/admin/ for
the organizer console (seed login `organizer` / `organizer`) and
http://localhost:8080/api/docs for the OpenAPI browser.

If port 8080 is already taken on your machine, publish on another host port:

```bash
DOGFOOD_PORT=9000 docker compose up
```

The portal still listens on 8080 inside the container, so update `base_url`
in `.dogfood.toml` to match if you run the checker against the new port.

## Run the acceptance checker

```bash
python3 spec/run.py .dogfood.toml > acceptance-report.txt
```

The committed `acceptance-report.txt` shows all seven checks passing at T1
and T2.

## The seed tokens

On boot the seed command prints four dev auth headers and they are already
copied into `.dogfood.toml`:

```
organizer   = "Authorization: Token organizer0000000000000000000000000000000"
judge_a     = "Authorization: Token judgea0000000000000000000000000000000000"
judge_b     = "Authorization: Token judgeb0000000000000000000000000000000000"
participant = "Authorization: Token participant00000000000000000000000000000"
```

These four tokens are dev and seed only. They exist so an offline checker
that never logs in can attach a per-role header. Do not carry them into a
real deployment. Rotate them by editing the seed command or deleting the
tokens in the admin.

## Roles and isolation

Every account has one role: visitor, participant, judge, organizer or admin.
The role is the security boundary, enforced by DRF permission classes in
`src/core/permissions.py`.

- The gallery is public.
- Submitting after the deadline is refused with 403. The fixture event
  closed on 2026-03-01, so the seeded portal is already closed.
- `GET /api/judge/scores` returns the caller's own scores. A judge sees only
  their own. An organizer or admin may read another judge's scores by
  passing `?judge_id=<id>`. A judge asking for a peer by id is refused with
  403. That is the check most portals fail, so it lives in the backend and a
  curl as the wrong judge is turned away.
- `GET /api/export/results.csv` is organizer only. It carries the weighted
  per criterion means and a weighted composite, so the criterion weights an
  organizer set are visible in the export.

## Route map

| Name | Path | Who |
| --- | --- | --- |
| gallery | `/projects` | public |
| submit | `POST /api/projects` | participant, refused when closed |
| judge scores | `/api/judge/scores` | judge, organizer, admin |
| peer scores | `/api/judge/scores?judge_id=<id>` | own judge or organizer/admin |
| csv export | `/api/export/results.csv` | organizer, admin |
| health | `/health` | public |
| api docs | `/api/docs` | public |

## Layout

```
src/            Django project (dogfood) and the core app
  core/models.py        the schema
  core/permissions.py   role isolation
  core/api_views.py     judge scores, submit, CSV export
  core/management/commands/seed.py   loads fixtures, prints tokens
spec/           the acceptance checker, fixtures and this program's spec
.dogfood.toml   points the checker at the portal
docker-compose.yml   one command to a seeded, offline portal
```

## Tiers claimed

T1 and T2, verified by `acceptance-report.txt`. The schema already carries
the T3 and T4 tables (votes, comments, audit log, tokens) so the higher
tiers build on top without a migration rewrite.

## License

MIT. See `LICENSE`.
