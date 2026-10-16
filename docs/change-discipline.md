# Change discipline

The rules every code change in this repo obeys. This is the doc `.aiworkflowrc` names as
`design_philosophy`: it is handed to every `code-writer`, `code-reviewer`, `plan-writer` and
`plan-reviewer` the pipeline dispatches, and it is what a reviewer cites when sending work back.

The design is not here. It is the founding design in the spec repo,
`../NotificationsSpecs/handovers/ntfy-notifications-2026-10-10.md`, with its research companion
beside it. What it marks *(ruled)* is binding. A decision it leaves open is the operator's to rule,
never a plan's or an implementation's to assume. As code builds what it describes, the design as
built goes into topic docs under `docs/`, which stand on their own: this repo is public and the spec
repo is not.

## One workspace, one directory per app

The repo is a uv workspace, and each deployable app is a member: a top-level directory that is its
own uv project (its `pyproject.toml`, its package, its `tests/`, its `Dockerfile`), listed in the
root `pyproject.toml`'s `members`. Code that two apps share goes into a library member under
`packages/`, never into a copy. The root holds the one ruff and pytest configuration and the one
`uv.lock`; a member carries neither.

An app's image installs with `uv sync --frozen --no-dev`, with uv copied from
`ghcr.io/astral-sh/uv`. Its build context is the repository root, because the lock lives there.

## The bridge

### Its API is a contract

A sender holds a bearer token for its registration and calls *submit* and *dismiss*. It never
learns where its notifications go: that is the registration's configuration. The adapters that turn
Jenkins, Alertmanager and KubeCoder events into those calls live with their producers in other
repos, and they code against the API reference in `README.md`. So:

- A test pins the API (paths, fields, limits, status codes, the per-channel result), so a change to
  it is a visible diff in the slice that makes it, never a side effect.
- A change to it is deliberate and additive where it can be, and a slice that changes it says so in
  its close-out report, because the adapters have to follow.

Behind the API, internal interfaces change cleanly: change the code and fix the callers, with no
shim, adapter or compatibility parameter.

### Channels fail independently, and nothing retries

Each channel is attempted on its own. A failure on one never costs the other, and the response
reports each. There is no queue, no retry and no background redelivery: a sender that cares logs
the body. A failure is counted and logged, never swallowed.

One exception, and it is not a redelivery. When Telegram refuses a send or an edit that carries a
submit's Open button as a bad request (`400`), other than for a message that is unchanged or gone,
the bridge makes that call once more without the button, at once and within the same submit. The
message lands without Open, the chat's outcome is that second call's, and the refusal is logged at
`warning`. No other call is made twice, and no failure of the second call is retried.

### Registrations are configuration

Registrations (tokens, topics, chats, icons) come from the deploy repo's values and Secrets, never
from code. The bridge refuses to start on an invalid registration set rather than run with part of
it.

### State holds ids, not content

The bridge's SQLite stores what an update needs (message ids, ntfy generations, priorities,
timestamps) and no title or message text. A change that wants to store content reopens a ruling,
and that is the operator's.

## No secrets in this repo

This repo is public. No secret, token or chat id goes into a commit, and test fixtures are invented.
Credentials reach an app as environment variables at run time.

## No tombstones

Delete replaced code completely: no "moved to X" comments, forwarding stubs, deprecated aliases,
commented-out blocks or helpers kept in case they come back. The same holds for prose: when a
convention changes, rewrite the doc rather than appending a note that the old rule no longer holds.

## Validate at the boundary, nowhere else

No `try`/`except` that swallows an error, no silent fallback, no retry or cache without an observed
failure to point at, and no guard for a condition the types already rule out.

The boundaries are where checking is the feature: an API request (its bearer token, its body's shape
and limits), the configuration an app reads at start, and what ntfy and Telegram answer. An input
validated at the boundary is not checked again further in.

## Testability is critical

Every change ships with a test. A feature without one is incomplete, and "verified by hand" is no
substitute: the point of a test is that it runs again next time.

`kc project test` is hermetic. It reaches no ntfy, no Telegram and no network: each channel is a
fake that records its calls and can be scripted to refuse. It is green from any environment with no
credentials. Checks against a real ntfy or Telegram belong to the test phase
([`slice-test-plan.md`](slice-test-plan.md)), not to the suite.
