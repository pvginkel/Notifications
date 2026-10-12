# Slice documentation plan

Which documentation surfaces a shipped slice brings up to date, and the rules for each. This is the
doc `.aiworkflowrc` names as `doc_phase.plan`: the run loop's doc phase is "read this and execute
it", working from the whole slice's merged diff.

Work the diff, not a checklist. A surface below is owed an update only when the slice's changes
actually reached it.

## The surfaces

### 1. `README.md`

The public face, and the bridge's API reference: the adapters in other repos code against it, so it
carries the endpoints, their fields and limits, the status codes, the per-channel result, and what a
sequence id and a priority do. A slice that changes the API brings it up to date in the same pass,
and a slice that adds an app adds that app's section. Until then it holds the repo's layout and how
to run the gates.

### 2. `docs/`

Small topic docs, one subject each, for what the README does not carry: an app's configuration (the
registrations file, its environment), its state, its rendering rules, its metrics. The design in
the spec repo is the input the apps are built from, not their documentation: as slices build what it
describes, the design *as built* lands here, and a topic doc never points a reader at the spec repo
for how something works. This repo is public and the spec repo is not, so a doc here has to stand on
its own.

The change discipline, this plan and the slice testing strategy live here too. A slice that changes
what they describe (an app that is now runnable, an image CI now builds, a deployment that now
exists) updates them. A step added to the testing strategy is run once before it is written down.

### 3. `CLAUDE.md`

Loaded every turn by every agent the pipeline spawns, so it stays at about one screen and states
each fact once. When a new standing rule belongs there, something else moves out to a `docs/` topic
doc so that the file does not grow. Nothing the pipeline reads by machine goes in it; that is
`.aiworkflowrc`'s job.

### 4. `.kubecoder/project.yaml`

Its comments say why each verb is wired or left out ("no `test:` yet: no app"). A slice that wires a
verb or adds a component keeps those comments true.

### 5. Other repos

`NotificationsDeploy` is checked out beside this repo, and a slice that changed it brings its
`README.md` up to date in the same pass. Everything else lives with its owner: the adapters
(DockerImages, KubeCoder), the runbooks and decisions in the Ansible repos, KubeCoder's docs. This
phase edits none of them. A slice that changed the API, or anything a deployment has to follow,
names it in the close-out report so the operator can file the change against its owner.

## What "up to date" means here

**State the design as it is**, as implemented, not as the slice planned it. Where the implementation
diverged from the plan or from the design, the doc describes what shipped, and the close-out report
names the divergence. No changelog entries, no "as of slice NNN", no tombstones for superseded
conventions: rewrite the doc instead, per [`change-discipline.md`](change-discipline.md).

**Ground every claim in the shipped source.** A doc sentence that cannot be checked against the
merged tree does not go in.

**Nothing private goes in.** No secret, token, chat id or real registration: examples are invented.

## Gates

```bash
kc project lint
```

It must exit `0`. It also proves the doc phase moved no code, and `ruff format --check` covers the
Python code blocks in Markdown, so a README example that is not formatted fails it. Read the exit
code directly, never through a pipe.

Check that relative links resolve. Then commit. If the slice's own folder in `../NotificationsSpecs`
needs anything, commit there separately: it is a separate git repo, and a shared working tree, so
stage files by name.

## When there is little to do

A slice that changed no design, no convention and no reader-facing surface owes nothing here, and
saying so plainly is the correct outcome. Do not invent doc work to fill the phase.
