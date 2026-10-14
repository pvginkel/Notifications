# Slice testing strategy

How a slice is proven once its phases are merged. This is the doc `.aiworkflowrc` names as
`test_phase.strategy`: the run loop's test phase is "read this and execute it", and nothing else
names it. Read it top to bottom and do what it says.

## What this phase proves today

The gates of this repo and of `NotificationsDeploy`, a by-hand build of every image the slice
touched, the bridge's local live check against a fake ntfy, then the pushes: `NotificationsDeploy`'s
and this repo's, with the Jenkins build this repo's starts: the image in the registry and its pin in
`NotificationsDeploy`. Argo CD deploys `NotificationsDeploy`'s `main` to prd, so the pushes deploy,
and the deployed checks prove the platform live after them, through the real ntfy and Telegram.
Each step is executed once before it is written down ([`slice-doc-plan.md`](slice-doc-plan.md) says
so). There is no dev instance and no `devlock`.

## 0. Preconditions

The driver has ff-merged every code phase into its repo's base branch, `main` here and in
`NotificationsDeploy`. The two checkouts sit side by side, `../NotificationsDeploy` beside this one,
each with `main` checked out at the commit being shipped. Confirm both trees are clean
(`git status --short`, `git -C ../NotificationsDeploy status --short`) before starting. A dirty tree
here means an earlier phase left something behind, and that is a finding, not something to tidy
away.

`kc project` is cwd-bound. This repo's verbs run from its root, as does every command below;
`NotificationsDeploy`'s verbs run from that repo's root, in a `(cd ../NotificationsDeploy && …)`
subshell.

## 1. The gates

```bash
kc project build
kc project test
kc project lint
```

Read each exit code directly, never through a pipe, which hands back the last command's code, not
the verb's. `0` is green. `3` means no statement ran, which is neither a pass nor a failure. The
manifest declares no `build:`, because Jenkins builds the images (section 4) and section 2 proves
them by hand, so `build` exits `3`: the close-out report says it ran nothing, never that it is green.
`test` runs the suite and `lint` the linter: each must exit `0`, and a `3` from either means the
verb was unwired, which is a finding. No gate is known red, so a failure is this slice's.

Run the suite once directly too, `cexec python uv run --all-packages pytest`, and read the count
off its summary line: `[ OK ]` looks the same for an empty suite as for a full one.

### `NotificationsDeploy`'s gates

```bash
(cd ../NotificationsDeploy && kc project lint); echo "exit=$?"
(cd ../NotificationsDeploy && kc project test); echo "exit=$?"
```

They run on every slice, whether or not it touched `NotificationsDeploy`: its `test` generates the
`notifications-deploy` architecture artifact with this repo's `docs/architecture/architecture.yaml`
overlaid through `../Notifications`, so a change here can turn it red there. Their exit codes read as
this repo's do: each must exit `0`, and a `3` is an unwired verb, which is a finding. No gate is
known red.

`lint` resolves the chart's library dependency, then runs `helm lint` on the prd values and
`terraform fmt -check`. `test` renders the chart as Argo CD's Application does and asserts on the
objects, validates the Terraform and plans it against mocked providers, then generates the
architecture artifact, validates it, and asserts that ntfy serves the bridge and holds the public
host. Each test script prints an `ok:` line into the log `kc` names on its last line
(`full output: …`). Unlike this repo's suite, `test` reaches the estate's services: the library
chart from `https://charts.home`, the Terraform providers through the `iac` container's mirror, and
the published architecture dataset at `architecture.webathome.org`. A failure the log shows to be
one of them unreachable is a check that cannot run (see Findings), not a red gate.

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

## 4. The pushes, and the build this repo's starts

Pushing is this phase's job. The driver ff-merges locally and never pushes a code phase, then checks
before the doc phase that every repo in `state.json`'s `bases` reached `origin`. Push each one,
honouring any repo named in `plan.md`'s `## Push holds`, and push `NotificationsDeploy` before this
repo: this repo's push starts a build that ends by committing a pin to `NotificationsDeploy`'s
`origin/main`, which a later `NotificationsDeploy` push would have to take in first.

Both pushes deploy, and section 5 proves what they deployed. Read the Application before them, so
that what section 5 finds is the pushes':

```bash
cexec iac kubectl --context prd -n argocd-prd get application notifications-prd -o jsonpath='{.status.sync.status} {.status.health.status}{"\n"}' </dev/null
```

It reads `Synced Healthy`. Any other state is a finding the pushes did not cause, and the close-out
report says so.

### `NotificationsDeploy`

A push of its `main` deploys. Argo CD's registry, ArgoCDDeploy's `releases/values.yaml`, carries
`notifications`, so the Application `notifications-prd` syncs `NotificationsDeploy`'s `main` to prd,
its PreSync hook applying the stage's Terraform first. The push also starts a build of
`AaC/NotificationsDeploy`, the repo's architecture producer, through the repo's Jenkins push hook.

Read what there is to push, and what origin gained meanwhile:

```bash
git -C ../NotificationsDeploy fetch origin
git -C ../NotificationsDeploy rev-list --count origin/main..main
git -C ../NotificationsDeploy log --format='%h %an %s' main..origin/main
```

The count is the commits this slice has to push. With `0` there is nothing to push, and the close-out
report says so. The log lists what `origin/main` carries that `main` lacks, and it holds pin commits
alone: every green build of this repo adds one, by `jenkins`, reading
`ci: image pins from Notifications #<n>`, which changes nothing but the line
`  notificationBridge: ':<n>'` in `config/prd/values.yaml`. Any other commit there is a finding, and
`NotificationsDeploy` is not pushed: settling it is the operator's. With the log empty, push.
Otherwise bring `main` onto `origin/main` first:

```bash
git -C ../NotificationsDeploy rebase origin/main; echo "exit=$?"
```

`exit=0` is a clean rebase. A slice commit that rewrote the lines around the pin conflicts with a pin
commit: `CONFLICT (content): Merge conflict in config/prd/values.yaml` and `exit=1`. Origin's pin
wins, and the slice's lines around it stay. At each stop that conflicts there, take the replayed
commit's file (in a rebase, `--theirs` is the commit being replayed), put origin's pin line back, and
continue:

```bash
pin=$(git -C ../NotificationsDeploy show origin/main:config/prd/values.yaml | grep '^  notificationBridge:')
git -C ../NotificationsDeploy checkout --theirs config/prd/values.yaml
sed -i "s|^  notificationBridge: .*|$pin|" ../NotificationsDeploy/config/prd/values.yaml
git -C ../NotificationsDeploy add config/prd/values.yaml
GIT_EDITOR=true git -C ../NotificationsDeploy rebase --continue; echo "exit=$?"
```

With pin commits alone in the log, that loses nothing of origin's. A conflict anywhere but the pin
line is a finding: run `git -C ../NotificationsDeploy rebase --abort`, and `NotificationsDeploy` is
not pushed. After the rebase the tree differs from the one section 1 gated by origin's pin alone,
which the gates hold to a `:<build>` suffix, never to a number:

```bash
git -C ../NotificationsDeploy diff ORIG_HEAD main
```

It shows the pin line's change and nothing else. Push, then read that origin has every commit:

```bash
git -C ../NotificationsDeploy push origin main; echo "exit=$?"
git -C ../NotificationsDeploy fetch origin
git -C ../NotificationsDeploy rev-list --count origin/main..main
```

It passes when the push exits `0` and the count reads `0`.

### This repo, and the build it starts

A push of this repo's `main` starts a build of the Jenkins job `Notifications`, at Jenkins' root,
through the repo's push hook, and one of `AaC/Notifications`, the repo's architecture producer. The
`Notifications` build runs the suite, builds
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
`ci: image pins from Notifications #<n>`, and the file reads `notificationBridge: ':<n>'`. That pin
commit is a push to `NotificationsDeploy`'s `main`, and section 5 proves its deploy.

## 5. The deployed checks

Argo CD's Application `notifications-prd`, in `argocd-prd`, deploys `NotificationsDeploy`'s `main`
into the namespace `notifications-prd`: one pod of the Deployment `notifications`, holding ntfy, the
bridge and the icons' nginx. These checks prove it live after section 4's pushes, on the commit
`NotificationsDeploy`'s `origin/main` holds then, which is this repo's pin commit when this repo was
pushed. With neither repo pushed, nothing new is deployed, and the close-out report says this
section did not run.

The checks run from this environment, each with the access it names and no more:

- `k`: the default kubeconfig. It reads, and it cannot exec or read a Secret.
- `kw`: `~/.kube/config-prd-write`, cluster-admin. Only the submits (5.8) take it, for `exec`.
- `bao`: OpenBao's `openbao-admin` AppRole, which the Ansible checkout's `scripts/bao-login.sh`
  logs in with, from that repo's vault. Listing and metadata only: no command here reads a value.
- `gh`: the environment's GitHub token, admin on `NotificationsDeploy`, which reading its hooks
  takes.
- No credential: Prometheus at `http://prometheus.home`, and ntfy's public vhost.
  `https://prometheus.home` serves another site.

A check whose access the environment lacks cannot run (see Findings). Set up the shell once, in this
repo's root:

```bash
k()  { cexec iac kubectl --context prd "$@" </dev/null; }
kw() { cexec iac kubectl --kubeconfig "$HOME/.kube/config-prd-write" --context prd -n notifications-prd "$@" </dev/null; }
. /work/Ansible/scripts/bao-login.sh
rev=$(git -C ../NotificationsDeploy rev-parse origin/main)
```

`$rev` is the commit the checks prove, read after section 4's last `fetch`.

### The deploy has synced (k)

Wait for the Application's sync of `$rev` to succeed, then for the pod's rollout:

```bash
timeout 15m sh -c "until cexec iac kubectl --context prd -n argocd-prd get application notifications-prd \
  -o jsonpath='{.status.operationState.syncResult.revision} {.status.operationState.phase} {.status.sync.status} {.status.health.status}' \
  | grep -qx '$rev Succeeded Synced Healthy'; do sleep 15; done"; echo "exit=$?"
k -n notifications-prd rollout status deployment/notifications --timeout=10m; echo "exit=$?"
```

Each reads `exit=0`, and the second `deployment "notifications" successfully rolled out`. Argo CD
syncs only a commit that changes what the chart renders: after a push that changes nothing there,
the first reads `exit=124`, and 5.2 shows the Application `Synced` at `$rev` with no new operation.
Any other `exit=124` is a finding.

### 5.1 Both leaves exist (bao)

```bash
cexec iac bao kv list kv/eso/prd/notifications/prd </dev/null
for leaf in ntfy-auth registrations; do
  cexec iac bao kv metadata get -format=json kv/eso/prd/notifications/prd/$leaf </dev/null \
    | jq -c --arg leaf $leaf '{leaf: $leaf, current_version: .data.current_version, created_time: .data.created_time}'
done
```

The list reads `ntfy-auth` and `registrations`, and each leaf has a `current_version` and a
`created_time`. The go-live seeded both at version 1; a re-seed or a rotation raises it.

### 5.2 The Application (k)

```bash
k -n argocd-prd get application notifications-prd -o json | jq -c '{sync: .status.sync.status,
  revision: .status.sync.revision, health: .status.health.status, op: .status.operationState.phase,
  dest: .spec.destination.namespace, syncOptions: .spec.syncPolicy.syncOptions}'
```

It passes on `Synced`, `$rev`, `Healthy`, `Succeeded`, `notifications-prd` and `syncOptions`
`null`: server-side apply is off.

### 5.3 The PreSync hook applied the stage's Terraform (k)

```bash
k -n argocd-hooks logs job/tf-presync-notifications-prd --all-containers | grep -E 'Plan:|No changes|Apply complete|presync: [0-9]'
k -n argocd-hooks logs job/tf-presync-notifications-prd --all-containers | grep -ci error
```

The stage's Terraform is the state volume's RBD image and its static PV,
`notifications-prd-state-pv`, and the GitHub webhook to `deploy-hooks.webathome.org`
(`NotificationsDeploy`'s `terraform/`). A sync that changes none of it reads `No changes.`, then
`Apply complete! Resources: 0 added, 0 changed, 0 destroyed.` and `presync: 0 volume(s) reattached`.
A slice that changes it reads its own plan's `Plan:` line instead, and the matching `Apply complete!`
line. The count reads `0`: no line holds `error`.

### 5.4 The deploy webhook (gh)

```bash
gh api repos/pvginkel/NotificationsDeploy/hooks --jq '.[] | {id, active, url: .config.url, last: .last_response.code}'
```

Two hooks, both active, each last answering `200`: Jenkins' to
`https://jenkins.webathome.org/github-webhook/`, which builds `AaC/NotificationsDeploy`, and the
Terraform's to `https://deploy-hooks.webathome.org/api/webhook`, which tells Argo CD of a push.

### 5.5 The ExternalSecrets, the pod and the bridge's image (k)

```bash
k -n notifications-prd get externalsecrets
k -n notifications-prd get pods -o json | jq -c '.items[] | {ready: (.status.conditions[]
  | select(.type=="Ready").status), containers: [.status.containerStatuses[] | {name, image, ready, restartCount}]}'
git -C ../NotificationsDeploy show "$rev:config/prd/values.yaml" | grep 'notificationBridge:'
```

It passes when:

- `ntfy-auth` and `registrations` read `SecretSynced`, Ready `True`, which proves every key the two
  read;
- the one pod is Ready, its three containers ready with 0 restarts: `notification-bridge` on
  `registry:5000/notification-bridge` at the tag the last command prints, `ntfy` and `nginx` on the
  chart's digests. The pod pulling the image is the proof that the tag exists.

### 5.6 The public vhost (no credential)

```bash
u=https://ntfy.webathome.org
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' $u/
echo | openssl s_client -connect ntfy.webathome.org:443 -servername ntfy.webathome.org 2>/dev/null \
  | openssl x509 -noout -subject -issuer -enddate
curl -sS -w ' %{http_code}\n' $u/v1/health
curl -sS -w ' %{http_code}\n' "$u/kubecoder-dev/json?poll=1"
curl -sS -w ' %{http_code}\n' -d 'anonymous publish check' "$u/deployed-anon-check"
curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' $u/metrics
curl -sS $u/metrics | grep -c '^# \(HELP\|TYPE\)'
```

It passes when:

- `/` answers `200 verify=0`, and the certificate's subject is `CN=ntfy.webathome.org`, its issuer
  Let's Encrypt, and its `notAfter` still ahead;
- `/v1/health` answers `{"healthy":true} 200`;
- the anonymous read of `kubecoder-dev` and the anonymous publish to a throwaway topic each answer
  `{"code":40301,"http":403,"error":"forbidden",…} 403`;
- `/metrics` answers `200 text/html; charset=utf-8`, ntfy's web-app page, as any topic name does,
  and the count is `0`: no metric line. ntfy's metrics listen on port 9090, which only the Service
  `ntfy` exposes, so the vhost serves no metric.

### 5.7 Prometheus scrapes both Services, and the rules are inactive (no credential)

```bash
curl -sS -G http://prometheus.home/api/v1/query --data-urlencode \
  'query=up{job="kubernetes-service-endpoints", namespace="notifications-prd", service=~"ntfy|notification-bridge"}' \
  | jq -c '.data.result[] | {service: .metric.service, instance: .metric.instance, value: .value[1]}'
curl -s 'http://prometheus.home/api/v1/rules?type=alert&rule_group[]=notifications' \
  | jq -c '.data.groups[].rules[] | {name, state, health}'
```

It passes when `up` is `1` for `ntfy` (`:9090`) and `notification-bridge` (`:8090`), on the pod's
address, and `NtfyDown`, `NotificationBridgeDown` and `NotificationDeliveryFailing` read
`inactive`, health `ok`. Read the rules again after 5.8: they still are. The scrape interval is
1m.

### 5.8 The submits, inside the bridge container (kw)

Two submits and their dismisses, as the `kubecoder-dev` registration, through the real ntfy and
Telegram: the messages land on that registration's ntfy topic and in the KubeCoder dev bot's
Telegram chat, and the dismisses delete them. The registration's token never leaves the pod. Two
Python helpers run in the bridge container through `exec`, post to its own loopback API and read the
token from the container's own environment, so the command line carries the variable's name and
never its value. No command here prints the container's environment, and nothing written here holds
a value. Write the helpers under `tmp/`, which git ignores:

```bash
mkdir -p tmp/deployed
cat > tmp/deployed/submit.py <<'EOF'
import os, sys, urllib.error, urllib.request
path, body = sys.argv[1], sys.argv[2]
request = urllib.request.Request(
    "http://127.0.0.1:8090" + path,
    data=body.encode(),
    method="POST",
    headers={
        "Authorization": "Bearer " + os.environ["KUBECODER_DEV_TOKEN"],
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode())
EOF
cat > tmp/deployed/metrics.py <<'EOF'
import urllib.request
text = urllib.request.urlopen("http://127.0.0.1:8090/metrics", timeout=10).read().decode()
for line in text.splitlines():
    if line.startswith(("notifications_total{", "notifications_open_rows{")) and "kubecoder-dev" in line:
        print(line)
EOF
```

Then submit, read the metrics after each step, dismiss, and read the bridge's log:

```bash
x() { kw exec deploy/notifications -c notification-bridge -- python -c "$(cat "$1")" "${@:2}"; }
x tmp/deployed/metrics.py
x tmp/deployed/submit.py /v1/submit '{"sequence_id": "deployed-check-state-row", "title": "Notifications deployed check", "message": "A submit with a sequence id, to prove it writes a state row. It is dismissed in a moment."}'
x tmp/deployed/metrics.py
x tmp/deployed/submit.py /v1/submit '{"sequence_id": "deployed-check-blockquote", "title": "Notifications deployed check", "message": "> A blockquote holding a [link](https://ntfy.webathome.org/v1/health) and `inline code`, which Telegram has to accept."}'
x tmp/deployed/metrics.py
x tmp/deployed/submit.py /v1/dismiss '{"sequence_id": "deployed-check-state-row"}'
x tmp/deployed/submit.py /v1/dismiss '{"sequence_id": "deployed-check-blockquote"}'
x tmp/deployed/metrics.py
kw logs deploy/notifications -c notification-bridge --since=5m
```

The first read is the baseline: a fresh pod's counters start at 0, and the open rows are the state
volume's, 0 while no sender holds a sequence open. It passes when:

- the state-row submit answers `200 {"ntfy":"ok","telegram":"ok"}`, and
  `notifications_open_rows` rises by 1 on `ntfy` and by 1 on `telegram`: the ntfy row and one row
  per Telegram chat, and `kubecoder-dev` has one chat;
- the blockquote submit answers `200 {"ntfy":"ok","telegram":"ok"}`: Telegram accepted a
  blockquote holding a link and code. `notifications_total{channel="telegram",outcome="ok"}` has
  risen by 2 and the open rows by 2 on each channel;
- each dismiss answers `200 {"ntfy":"ok","telegram":"ok"}`. The open rows are back at the baseline,
  `notifications_total` with `outcome="ok"` has risen by 4 on each channel, and with
  `outcome="error"` by 0;
- the bridge's log holds the four `submit` and `dismiss` lines at level `info`, each outcome `ok` on
  both channels, and no `warning`.

### 5.9 Back to zero, with no error sample (no credential)

```bash
curl -sS -G http://prometheus.home/api/v1/query --data-urlencode \
  'query=sum(notifications_open_rows{namespace="notifications-prd", registration="kubecoder-dev"})'
curl -sS -G http://prometheus.home/api/v1/query --data-urlencode \
  'query=max_over_time(sum by (registration, channel) (notifications_total{namespace="notifications-prd", outcome="error"})[15m:15s])'
```

Prometheus scrapes every minute, so the sum shows the submits' rows until the first scrape after
the dismisses: read it again until it is back at 5.8's baseline. The error counter's maximum over
the checks is `0` for every registration and channel pair. Then read 5.2 again: the Application is
still `Synced` and `Healthy` at `$rev`.

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
