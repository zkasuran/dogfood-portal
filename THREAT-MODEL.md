# Threat model

What this platform protects, who could attack it and what stops them. Every mitigation named
here is enforced in the backend or the signed bytes, not in the UI. The load-bearing ones
are covered by tests and the acceptance suite. This is the Threat Model bonus and it is the
defence behind the Judging Integrity score.

## Assets

- The integrity of the ranking: the published result must follow from the raw scores by the
  documented method, with nobody able to alter it undetected.
- The confidentiality of in-progress judging: a judge's scores are private from other judges.
- The fairness of community voting: one real person, one unit of influence.
- The availability of a clean audit: an organizer can reconstruct every number and every
  intervention.

## Actors and trust

Visitor and participant are untrusted. A judge is trusted only within their assigned tracks and
only for their own scores. Organizer and admin are trusted operators. The deployment holds one
Ed25519 signing key, generated on first boot into its own volume, never exposed.

## Threats and mitigations

### 1. A judge reads another judge's scores or the aggregate
Enforced in the backend. The scores endpoint returns only the caller's own scores from the
authenticated identity. The endpoint that names a specific judge refuses a caller who is not
that judge or an organizer. A second judge probing a peer's URL gets 403, a participant gets 403,
an anonymous request gets 401. A judge is further scoped to their assigned tracks. A curl with the
wrong role fails exactly as a browser would, which the acceptance suite verifies.

### 2. A participant or visitor escalates to judge or organizer actions
Role is checked on the view, not painted on the page. Every privileged read or write requires the
matching role, so there is no UI-only gate to bypass with a direct request.

### 3. A submission arrives after the deadline
The submission endpoint checks the event's close time in the model layer and refuses a late post
with a 4xx, so a closed event cannot be reopened by hitting the API directly.

### 4. Community vote manipulation: Sybil, ballot stuffing, brigading
One vote per verified identity is a schema constraint, not a front-end check. Access is
configurable (open link, email gated or authenticated) so an organizer can raise the cost of a
fake identity. Rate limits and duplicate detection blunt automated stuffing, judge scores are
weighted above the popular vote so a brigade cannot by itself decide the outcome. Every vote
is written to an append-only audit an organizer can read. Quadratic voting is offered as an
option, stated honestly: it resists a loud minority but it is not Sybil proof. Under real
collusion its fairness degrades, so it is a tool an informed organizer chooses rather than a
guarantee.

### 5. Judge collusion or bias
A judge might inflate their own team or track. A judge might score everyone the same to lift the
average. Both are handled by the method rather than by trust. Self-review is blocked in the backend
and recorded. The normalization removes each judge's leniency, so systematic inflation is
corrected rather than counted. A judge with no score variance contributes a bias offset and zero
ranking signal. That judge is flagged as low information in the audit. The pairwise mode's reliability
estimate flags a judge whose comparisons are close to random. None of these rely on the judge
behaving well.

### 6. Result tampering
The published bundle carries the raw scores, the rubric and weights, the method and its
parameters, the code commit and the ranking, hashed over a canonical encoding and signed with the
deployment key. The standalone verifier recomputes the ranking from the raw scores, checks the
hash and verifies the signature. Changing one score, the ranking or any byte of the payload
fails verification. So a tampered result is detectable by anyone, with no trust in the host and no
key of ours.

### 7. Duplicate submission gaming
A team submitting twice cannot be ranked twice or split its reviews. Duplicates are detected and
merged to one canonical project before scoring, with the merge recorded in the audit.

### 8. Abuse of the seeded credentials
The per-role tokens the seed prints exist so an offline checker can attach without a login. They
are development tokens, stated plainly as such in the README and the boot log. A real
deployment issues normal credentials. The whole thing runs offline by default, so there is no
external surface to abuse in the shipped demo.

## Residual risks and non-goals, stated plainly

- A compromised organizer or admin is outside this model. Those roles are trusted operators. An
  attacker who holds one holds the event.
- The seeded tokens are not a production auth posture. They are a convenience for the offline
  checker and are documented as such.
- Quadratic voting is not Sybil proof. It is offered with that caveat, not sold as a fix.
- A judge who only ever saw one track cannot be separated from that track's level, so the model
  cannot fully remove bias in that case. It surfaces the confound as a warning rather than hiding
  it.
