# Slice testing strategy

How a slice is proven once its phases are merged. This is the doc `.aiworkflowrc` names as
`test_phase.strategy`: the run loop's test phase is "read this and execute it", and nothing else
names it. Read it top to bottom and do what it says.

## What this phase proves today

The gates, a by-hand build of every image the slice touched, the bridge's local live check against
a fake ntfy, then the push and the Jenkins build it starts: the image in the registry and its pin
in `NotificationsDeploy`. Nothing is deployed yet, so section 5 is empty until the slice that
deploys the platform writes it. Each step is executed once before it is written down
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
failure. `0` is green. `3` means no statement ran, which is neither a pass nor a failure. The
manifest declares no `build:`, because Jenkins builds the images (section 4) and section 2 proves
them by hand, so `build` exits `3`: the close-out report says it ran nothing, never that it is green.
`test` runs the suite and `lint` the linter: each must exit `0`, and a `3` from either means the
verb was unwired, which is a finding. No gate is known red, so a failure is this slice's.

Run the suite once directly too, `cexec python uv run --all-packages pytest`, and read the count
off its summary line: `[ OK ]` looks the same for an empty suite as for a full one.

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

The bridge boots in the `python` tool container against a fake ntfy, and is proven by what it does:
a submit and a dismiss, `/healthz` and `/metrics`. It never reaches the real ntfy or Telegram. The
bridge's Telegram API URL is fixed, so the check's one registration has no `telegram` channel, and
the suite's fake Telegram is what proves that side. The tool containers share this pod's network:
what the `python` container serves answers on `127.0.0.1` here, and a process it starts under
`nohup … &` outlives the `cexec` call. Everything the check writes is under `tmp/live/`, which git
ignores.

Set it up: an icon file, an empty state directory (the bridge does not create it), a registrations
file whose token is a `$NAME` placeholder, and the fake, which answers every publish and clear `200`
and prints each request as a JSON line:

```bash
rm -rf tmp/live && mkdir -p tmp/live/icons tmp/live/state && touch tmp/live/icons/live.png
cat > tmp/live/registrations.yaml <<'EOF'
registrations:
  live:
    token: $LIVE_TOKEN
    ntfy:
      topic: live
      icon: live.png
EOF
cat > tmp/live/fake_ntfy.py <<'EOF'
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Ntfy(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        record = {"method": self.command, "path": self.path, "headers": dict(self.headers)}
        print(json.dumps(record | {"body": body}), flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    do_PUT = do_POST

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), Ntfy).serve_forever()
EOF
```

Start the fake on port 18091, then the bridge on 18090, pointed at its own files by the `BRIDGE_*`
overrides, and wait for its health probe:

```bash
cexec python sh -c 'nohup python3 -I tmp/live/fake_ntfy.py 18091 > tmp/live/ntfy.log 2>&1 & echo $! > tmp/live/ntfy.pid'
cexec python sh -c 'LIVE_TOKEN=live-token NTFY_URL=http://127.0.0.1:18091 NTFY_TOKEN=live-ntfy-token \
  ICON_BASE_URL=https://icons.example BRIDGE_PORT=18090 \
  BRIDGE_REGISTRATIONS_FILE=tmp/live/registrations.yaml BRIDGE_STATE_DIR=tmp/live/state \
  BRIDGE_ICONS_DIR=tmp/live/icons \
  nohup uv run --all-packages notification-bridge > tmp/live/bridge.log 2>&1 & echo $! > tmp/live/bridge.pid'
timeout 30 sh -c 'until curl -fs http://127.0.0.1:18090/healthz; do sleep 1; done'; echo " exit=$?"
```

`ok exit=0` is the bridge up. On a timeout, `tmp/live/bridge.log` says why: a refused start reads
`notification-bridge refuses to start: …`, a held port `address already in use`. The two ports are
arbitrary; with one held, take another everywhere it appears.

Submit, read the metrics, dismiss, read them again:

```bash
curl -sS -w ' %{http_code}\n' http://127.0.0.1:18090/v1/submit -H 'Authorization: Bearer live-token' \
  -H 'Content-Type: application/json' -d '{"sequence_id": "live-1", "priority": "high",
  "title": "Live check", "message": "**bridge** live at step `submit`"}'
curl -fsS http://127.0.0.1:18090/metrics | grep -E '^notifications_(total|open_rows)'
curl -sS -w ' %{http_code}\n' http://127.0.0.1:18090/v1/dismiss -H 'Authorization: Bearer live-token' \
  -H 'Content-Type: application/json' -d '{"sequence_id": "live-1"}'
curl -fsS http://127.0.0.1:18090/metrics | grep -E '^notifications_(total|open_rows)'
cat tmp/live/ntfy.log
grep '^{' tmp/live/bridge.log
```

It passes when:

- the submit and the dismiss each answer `{"ntfy":"ok"} 200`, one key for the registration's one
  channel;
- the fake printed two requests. The first is `POST /live` carrying
  `Authorization: Bearer live-ntfy-token`, `X-Markdown: yes`, `X-Priority: 4`,
  `X-Icon: https://icons.example/live.png`, an `X-Title` (an RFC 2047 encoded word) and an
  `X-Sequence-ID` of 32 hex characters, with the message as its body. The second is
  `PUT /live/<that id>/clear`;
- after the submit, `notifications_open_rows{channel="ntfy",registration="live"}` is `1.0` and
  `notifications_total` with `outcome="ok"` is `1.0`; after the dismiss they are `0.0` and `2.0`.
  `outcome="error"` stays `0.0`;
- after the start's `"event": "prune"`, the bridge's log holds a JSON line per call,
  `"event": "submit"` then `"event": "dismiss"`, each naming `live`, `live-1` and
  `{"ntfy": "ok"}`.

Stop both by their own pids, and wait for both ports to close:

```bash
cexec python sh -c 'kill $(cat tmp/live/bridge.pid) $(cat tmp/live/ntfy.pid)'
timeout 15 sh -c 'while curl -s -o /dev/null http://127.0.0.1:18090/ || curl -s -o /dev/null http://127.0.0.1:18091/; do sleep 1; done'; echo "exit=$?"
tail -n 1 tmp/live/bridge.log
```

`exit=0` is both stopped. `bridge.pid` holds the pid of `uv run`, which hands the signal on to the
bridge, so the bridge's log ends `Finished server process [<pid>]`.

## 4. Push, and the build it starts

Pushing is this phase's job. The driver ff-merges locally and never pushes a code phase, then checks
before the doc phase that every repo in `state.json`'s `bases` reached `origin`. Push each one,
honouring any repo named in `plan.md`'s `## Push holds`.

A push of this repo's `main` starts a build of the Jenkins job `Notifications`, at Jenkins' root,
through the repo's push hook. The build runs the suite, builds
`registry:5000/notification-bridge:<build number>` and `:latest`, and pins
`images.notificationBridge: ':<build number>'` into `NotificationsDeploy`'s `config/prd/values.yaml`
with a commit to its `main`. The build check follows that build to its result, then reads what it
left behind. The Jenkins API answers to `$JENKINS_USER` and `$JENKINS_TOKEN`, which the environment
holds. When this phase pushes nothing to this repo's `main`, no build starts, and the close-out
report says the build check did not run.

Before the push, read the job and its hook:

```bash
curl -fsS -u "$JENKINS_USER:$JENKINS_TOKEN" "$JENKINS_URL/job/Notifications/api/json?tree=nextBuildNumber,inQueue,lastBuild%5Bnumber,result,building%5D"
gh api repos/pvginkel/Notifications/hooks --jq '.[] | {id, active, events, url: .config.url}'
```

`nextBuildNumber` is the number the push's build gets, `<n>` below. With `inQueue` true, an earlier
push's build is still waiting for its number: wait until it is false and read again. The hook is
active, on `push`, to `https://jenkins.webathome.org/github-webhook/`. Without it the push starts no
build: that is a finding, and the build check is not verified.

Push, then follow the build with a waiter run in the background, under a `timeout` past the
pipeline's own 60 minutes. Its exit is the wait:

```bash
timeout 70m sh -c 'until curl -fsS -u "$JENKINS_USER:$JENKINS_TOKEN" "$JENKINS_URL/job/Notifications/<n>/api/json?tree=building" | grep -q "\"building\":false"; do sleep 30; done'; echo "exit=$?"
```

`exit=0` is the build finished. `exit=124` with no build `<n>` (its URL answers `404`) means the push
started nothing, and the hook's deliveries say whether GitHub sent it:
`gh api repos/pvginkel/Notifications/hooks/<hook id>/deliveries --jq '.[0:3] | .[] | {delivered_at, event, status_code}'`.
Read the result:

```bash
curl -fsS -u "$JENKINS_USER:$JENKINS_TOKEN" "$JENKINS_URL/job/Notifications/<n>/api/json?tree=number,result,description"
```

The build is green when `result` is `SUCCESS` and the description reads `exit=0`, section 1's passed
count and `0 failed`. A red build is a finding, and its log is
`$JENKINS_URL/job/Notifications/<n>/consoleText`. Then read the image and the pin:

```bash
curl -fsS http://registry:5000/v2/notification-bridge/tags/list
git -C ../NotificationsDeploy fetch origin
git -C ../NotificationsDeploy log -3 --format='%h %an %s' origin/main -- config/prd/values.yaml
git -C ../NotificationsDeploy show origin/main:config/prd/values.yaml | grep 'notificationBridge:'
```

It passes when the tags include `"<n>"` and `"latest"`, a commit by `jenkins` reads
`ci: image pins from Notifications #<n>`, and the file reads `notificationBridge: ':<n>'`.

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
