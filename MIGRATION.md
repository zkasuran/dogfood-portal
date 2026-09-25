# Migration in and out

An event moves between instances as one JSON file. `export_event` dumps an event
and everything that hangs off it. `import_event` loads that dump into a fresh
database. The same projects, teams, judges, scores and rubric come back.

This is the answer to the walled garden. An organizer who runs an event here can
take the whole event elsewhere. An organizer who has data elsewhere can bring it
in against the same schema.

## Export

Dump one event to a file by its external id:

    python manage.py export_event evt_01 > event.json

The dump is JSON on stdout, so it pipes straight to a file. Progress goes to
stderr, so it never lands in the file. `--output event.json` writes the file
directly. `--indent 2` pretty-prints it.

What travels: the event, its prizes, tracks, teams and their members, projects (a
duplicate is kept and flagged, never dropped), the weighted rubric with its
criteria, judge assignments, scores with every per-criterion value, votes and
comments, plus the judge and participant accounts the event needs.

What does not travel, by design: portal admin accounts and the dev/seed auth
tokens. Those are not event data. A fresh instance gets them from `seed`. Password
hashes on the exported accounts do travel, so a judge or participant login keeps
working after a move.

## Import

Load a dump into a database that is migrated but not yet seeded:

    python manage.py import_event event.json

It recreates every row in dependency order. Projects are created first with the
duplicate link unset, then the link is set once both rows exist, so a
self-referential `duplicate_of` never fails. The command is idempotent: every row
is matched on its natural key, so a second run does not duplicate anything.

## Round trip, end to end

From a seeded instance to a fresh one, with a throwaway SQLite file for the target:

    # 1. export the seeded event
    python manage.py export_event evt_01 > /tmp/event.json

    # 2. point at a fresh database and migrate it
    export DJANGO_DB_PATH=/tmp/fresh.sqlite3
    python manage.py migrate

    # 3. load the event
    python manage.py import_event /tmp/event.json

The counts match the source event: 41 projects (1 flagged duplicate, prj_41 merged
into prj_07), 126 scores, 378 score values, 126 judge assignments, 40 teams, 91
memberships, 8 tracks, 30 judges, 91 participants, 1 rubric, 3 criteria. The two
portal accounts (organizer, admin) are the only rows that do not carry over,
because they are not part of an event.

## After an import

The imported database has all the event data but no auth tokens, because tokens
are dev/seed only. To attach the offline checker to a fresh instance run `seed`
once. It creates the organizer and admin accounts, prints the four dev headers and
leaves the imported event data untouched (its get_or_create calls match the rows
already there).

## The awkward cases, handled

- Duplicate submission (prj_07 and prj_41, same team, title and repo): both rows
  export. On import the duplicate is relinked to its canonical sibling after both
  exist. It is flagged, never silently dropped.
- Zero-variance judges (jdg_07 scored three reviews all 4, jdg_01 one review all
  2): they are plain rows to the migration, so they export and import like any
  other. The judging engine handles them without a divide-by-zero, documented in
  JUDGING.md.
- Timestamps: `submitted_at` and every `created_at` are preserved, not reset to
  the import time.
- Weights and money: criterion weights and prize amounts travel as strings, so no
  decimal precision is lost through JSON.
