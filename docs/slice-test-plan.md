# Slice testing strategy

How a slice is proven once its phases are merged. This is the doc `.aiworkflowrc` names as
`test_phase.strategy`: the run loop's test phase is "read this and execute it", and nothing else
names it. Read it top to bottom and do what it says.

## What this phase proves today

No app is built yet, so this doc starts local: the gates, a by-hand build of every image the slice
touched, then the push. It grows with the slices that build the platform. The slice that makes an
app runnable writes its local live check into section 3, and the slices that give the repo a CI job
and a deployment write sections 4 and 5. Each step is executed once before it is written down
([`slice-doc-plan.md`](slice-doc-plan.md) says so). There is no dev instance and no `devlock`.

## 0. Preconditions

The driver has ff-merged every code phase into the base branch. Confirm the tree is clean
(`git status --short`) before starting. A dirty tree here means an earlier phase left something
behind, and that is a finding, not something to tidy away.

Every verb below runs from the repo root, because `kc project` is cwd-bound.

## 1. The gates

```bash
kc project build
kc project test
kc project lint
```

Read each exit code directly, never through a pipe: a `kc project` verb prints its output only on
failure. `0` is green. `3` means no statement ran, which is neither a pass nor a failure. While the
manifest declares no `build:` and no `test:` (no app exists yet), those two exit `3`, and the
close-out report says the slice's gate was lint alone rather than calling the build and the suite
green. A slice that added the first app and left `kc project test` at `3` has left its suite unwired,
and that is a finding. `lint` must exit `0`. No gate is known red, so a failure is this slice's.

Once a suite exists, run it once directly, `cexec python uv run --all-packages pytest`, and read
the count off its summary line: `[ OK ]` looks the same for an empty suite as for a full one.

## 2. The images

Build every Dockerfile the slice added or changed once by hand, from the repo root, which is the
build context because the workspace lock lives there:

```bash
kaniko --dockerfile <app>/Dockerfile --no-push > tmp/kaniko-<app>.log 2>&1; echo "exit=$?"
```

Read the log's `Sent … of context` line: a context far larger than the sources means the
`.dockerignore` misses something. Always `--no-push`: Jenkins owns the registry's tags, and a
`dev-*` or `prd-*` tag is never pushed from here. With no Dockerfile touched, this step is a skip,
and the close-out report says so.

## 3. The local live check

None is written yet: no app runs. The slice that makes an app runnable writes its check here, after
running it once. It boots the app in the `python` tool container, proves it through what it does
(a submit and a dismiss against fakes, the health and metrics endpoints) rather than through an open
port, and stops it by its own pid.

## 4. Push

Pushing is this phase's job. The driver ff-merges locally and never pushes a code phase, then checks
before the doc phase that every repo in `state.json`'s `bases` reached `origin`. Push each one,
honouring any repo named in `plan.md`'s `## Push holds`.

A push to `main` builds nothing yet: no Jenkins job builds this repo. When the slice that adds the
`Jenkinsfile` lands and the operator has created the job, this section gains the build check: the
job's result, read through the Jenkins MCP tools, and the image pin the build writes into
`NotificationsDeploy`.

## 5. The deployed checks

None yet: nothing is deployed. The slice that deploys the platform writes them.

## 6. Check off `verification.json`

Mark each acceptance criterion with the evidence that settled it: the command run and what it
returned, or the call made and what it answered. A criterion that nothing in steps 1–5 exercised has
not passed by inspection. It is either an untested criterion (a finding) or a check this doc is
missing.

## Findings

Blocking findings come back as appended phases. Findings below the bar go in the close-out report
for the operator to triage. A check that cannot run at all (no credential, a port already held, a
service unreachable) is reported as *not verified*, never as passed. The phase may end with a
criterion unproven if it says so. It may not end with one assumed.

## The operator gate

The operator's gate is the close-out report, after the run.
