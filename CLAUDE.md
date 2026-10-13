# Notifications

The notification platform's apps. The first is the notification **bridge**, one API over ntfy and
Telegram: a producer submits and dismisses notifications under its registration's bearer token, and
the bridge delivers them to the registration's ntfy topic and Telegram chats, updating and clearing
them by sequence id. More apps will join it. The bridge is built, in `bridge/`, and nothing is
deployed yet.

The design is `../NotificationsSpecs/handovers/ntfy-notifications-2026-10-10.md` (§3 the bridge,
§4 the deployment), with its research companion beside it. Read it before writing code. What it
marks *(ruled)* is binding, and a decision it leaves open is the operator's to rule.

## Repo structure

- **A uv workspace.** Each deployable app is a top-level directory and a workspace member with its
  own `pyproject.toml`, `tests/` and `Dockerfile`. Shared code goes in `packages/`. The root
  `pyproject.toml` holds the one ruff and pytest configuration, and `uv.lock` sits beside it.
  `kc project` has one component, `root`, which owns the whole Python gate.
- **`docs/`**: the change discipline, the slice testing strategy, the slice documentation plan, and
  the apps' topic docs as they land.

Other repos the work touches, checked out beside this one:

- **`../NotificationsSpecs`**: the spec repo (the design, the slices). It is a separate git repo
  and a working tree shared by parallel sessions: commit there separately, and stage files by name,
  never `git add -A`.
- **`../NotificationsDeploy`**: the deployment. ntfy and the bridge run in one pod, synced by Argo
  CD, and CI pins the images into its `config/prd/values.yaml`.
- The adapters live with their producers, not here: Jenkins' and Alertmanager's in DockerImages,
  KubeCoder's in the KubeCoder monorepo.

## Working rules

**Commit straight to `main`, small and often**, in this repo and the spec repo alike.

**This repo is public; the spec and deploy repos are private.** No secret, token or chat id goes
into a commit here, and test fixtures are invented.

## The dev pipeline

Code changes go through the `dev` plugin's slice workflow (`/dev:triage` → `/dev:plan-slice` →
`/dev:run-slice`), which the operator drives. The project's half of that contract is `.aiworkflowrc`
and `.kubecoder/project.yaml`; nothing the pipeline reads by machine belongs in this file. Issues
file under the YouTrack project **`N`** (Notifications).

## Key documentation

- [`README.md`](README.md): the bridge's API reference, which the adapters code against.
- [`docs/bridge-configuration.md`](docs/bridge-configuration.md): the bridge's environment,
  registrations file, state and image.
- [`docs/change-discipline.md`](docs/change-discipline.md): the rules every change obeys.
- [`docs/slice-test-plan.md`](docs/slice-test-plan.md): how a slice is verified before it is pushed.
- [`docs/slice-doc-plan.md`](docs/slice-doc-plan.md): which doc surfaces a shipped slice updates.
