# Notifications

The notification platform's apps. The first is the notification bridge: one API over ntfy and
Telegram. Producers submit and dismiss notifications through it; it delivers them to ntfy and
Telegram per registration.

Deployed by [NotificationsDeploy](https://github.com/pvginkel/NotificationsDeploy); its specs and
slices live in NotificationsSpecs.

## The notification bridge

A sender holds the bearer token of its **registration**. The registration, set up by the
deployment, says where its notifications go: an ntfy topic with the registration's icon, one or
more Telegram chats reached by the registration's own bot, or both. Those are the registration's
**channels**. A sender never names itself or its channels: the token selects them. How a deployment
configures the bridge is in [`docs/bridge-configuration.md`](docs/bridge-configuration.md).

### Endpoints

All on port 8090.

| Endpoint | Token | What it does |
|---|---|---|
| `POST /v1/submit` | yes | Shows a notification, or updates one by its sequence id |
| `POST /v1/dismiss` | yes | Takes a notification down by its sequence id |
| `GET /healthz` | no | Answers `200` with the text `ok` |
| `GET /metrics` | no | Prometheus metrics (see [Observability](#observability)) |

Submit and dismiss take `Authorization: Bearer <token>` and a JSON object as the body. The scheme
is case-insensitive. A missing header, another scheme or a token no registration holds is refused
with `401` before the body is read.

```bash
curl -sS "$NOTIFICATIONS_URL/v1/submit" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"sequence_id": "build-1234", "priority": "high", "title": "Build 1234 failed",
       "message": "**deploy** failed at step `helm upgrade`"}'
```

### Submit

`POST /v1/submit`:

| Field | Type | | Limit | Meaning |
|---|---|---|---|---|
| `message` | string | required | 4096 characters, not blank | The text, as Markdown |
| `title` | string | optional | 250 characters | Plain text; `""` is no title |
| `priority` | string | optional | `min`, `low`, `default`, `high` or `max` | Defaults to `default` |
| `sequence_id` | string | optional | 1 to 256 characters | What a later submit or dismiss names it by |
| `url` | string | optional | 2048 characters, `http` or `https` with a host, visible ASCII | The notification's link: what a tap opens on ntfy, and an Open button on Telegram |

- The limits count characters (Unicode code points), not bytes.
- A field sent as `null` is taken as omitted.
- A field the table does not list is refused, and so is a string that holds a lone surrogate
  (`"\ud800"`), which has no UTF-8 form.
- The priority names are lower case: `"HIGH"` is refused.
- A `url` holds only `!` to `~`: anything else, a space included, is percent-encoded. Beyond that,
  the bridge checks only its scheme and that it has a host.

### Dismiss

`POST /v1/dismiss` takes one field, the required `sequence_id`, 1 to 256 characters:

```json
{"sequence_id": "build-1234"}
```

### Status codes

| Status | When | Body |
|---|---|---|
| `200` | The body was valid and each of the registration's channels was tried | The [per-channel result](#the-per-channel-result) |
| `400` | The body is not JSON or not an object, holds an unknown field or a value of the wrong type, lacks the message or the sequence id it needs, has a blank message, a `sequence_id` outside 1 to 256 characters, a `title` over 250, a `url` over 2048 characters, holding anything but visible ASCII or lacking an `http` or `https` scheme or a host, an unknown priority or a string with a lone surrogate | `{"error": "<why>"}` |
| `401` | The token is missing, not a bearer token, or unknown. The answer carries `WWW-Authenticate: Bearer` | `{"error": "unknown bearer token"}` |
| `413` | The message is over 4096 characters and the rest of the body is valid | `{"error": "<why>"}` |
| `404`, `405` | Another path, or another method | Plain text |

A refused request reaches no channel. A `200` does not mean that anything was delivered: it carries
each channel's result, and a channel that failed says so there.

### The per-channel result

A `200` answers a JSON object with one key per channel the registration has, `ntfy` and
`telegram`. A channel the registration does not have has no key. Each value is `ok` or
`error: <why>`:

```json
{"ntfy": "ok", "telegram": "error: -100123456789: send: HTTP 403: Forbidden: bot was kicked from the group chat"}
```

- **ntfy's why** is `<step>: HTTP <status>: <ntfy's error>` when ntfy refused the call, or
  `<step>: <exception>: <text>` when it was not reached. The step is `publish` or `clear`.
- **Telegram's why** names each chat that failed: `<chat id>: <step>: HTTP <status>: <Telegram's
  description>`, or `<chat id>: <step>: <exception>: <text>`. The step is `send`, `edit` or
  `delete`. Telegram is `ok` only when every chat took the call.
- A call that fails in more than one step lists each, joined by `; `.

Each channel is tried on its own, ntfy first and then each chat in turn, and a failure in one never
costs another. Nothing is queued and nothing is retried, but for a Telegram call whose Open button
Telegram refused, which is made once more without it (see [Rendering](#rendering)). Each failure is
counted in `/metrics` and logged.

A call to ntfy or Telegram gives up when it stalls for 10 seconds, and the calls run one after the
other. A submit makes at most two calls to ntfy and four to Telegram per chat, and a dismiss one
and two. A sender that waits for the result sets its own timeout above that, or it can give up on
a notification that was delivered: with one chat, a submit can take about a minute when both
channels are slow.

### Sequence ids

- **One-shot.** A submit with no `sequence_id` shows a new notification on ntfy and sends a new
  message to every chat. It cannot be updated or dismissed.
- **Update.** The first submit under a sequence id shows its notification. A later submit under the
  same id updates that notification instead of adding one: see [Priorities](#priorities) for when
  that alerts.
- **Dismiss.** A dismiss clears the notification on ntfy and deletes the message in each chat. The
  sequence id is then free: a submit under it starts a new notification.

A sequence id belongs to its registration: two registrations that use the same id never touch each
other's notifications, also when they share an ntfy topic.

The bridge keeps no title, no text and no url, only what an update needs: per channel, the
priority of the sequence and where it is shown. That is kept until a dismiss, or until the sequence
has gone 90 days without a submit. After that the bridge has forgotten it. A submit under the id
then sends a new Telegram message and leaves the old one, and a dismiss leaves that message. On
ntfy, a forgotten sequence whose priority never rose is still updated and cleared in place.

- A dismiss for a sequence id the bridge holds nothing for still clears on ntfy, and is `ok` on
  Telegram without a call.
- When Telegram refuses both the delete and the fallback edit of a dismiss (below), the result says
  so, and the dismiss can be repeated.
- Resubmitting a sequence is safe: where a channel already shows it, the resubmit updates it, and
  where a channel failed to show it, the resubmit tries again. A one-shot resubmitted is a second
  notification.
- A rise's take-down of the old notification is not retried. When ntfy's clear fails, or Telegram's
  delete and its fallback edit both fail (see [Priorities](#priorities)), and the new notification
  goes out, the result says so and the old one stays beside the new: a resubmit or a dismiss acts
  on the new one only.
- Submits and dismisses of one sequence take turns: the second waits until the first has its result.

### Priorities

| Priority | ntfy priority | Telegram mark | Telegram notifies |
|---|---|---|---|
| `min` | 1 | ⚪ | no, sent silently |
| `low` | 2 | 🔵 | no, sent silently |
| `default` | 3 | none | yes |
| `high` | 4 | 🟠 | yes |
| `max` | 5 | 🚨 | yes |

On ntfy, the phone app decides what each priority does, by
[ntfy's priority scale](https://docs.ntfy.sh/publish/#message-priority). On Telegram, the mark
starts the first line of the message, before the title.

A submit under a sequence id compares its priority with the one the sequence has:

- **The same or lower: in place, silently.** ntfy replaces the notification under its id, and the
  phone does not alert again. Telegram edits the message, which never notifies. The mark follows the
  new priority.
- **Higher: a rise, which alerts.** ntfy clears the notification and publishes it under a new id,
  so the phone shows it as new and alerts at the new priority. Telegram deletes the message and
  sends it again, and the new message notifies unless its priority is `low` or `min`.

The sequence takes the new priority either way, so a sequence that went down can rise again.

Telegram refuses to delete a bot's message that is more than 48 hours old in a private chat. When
it refuses the delete of a rise, the old message is edited to *Superseded.* and the new one is sent
all the same. When it refuses the delete of a dismiss, the message is edited to *Dismissed.*

### Rendering

- **ntfy** gets the message as Markdown (`X-Markdown: yes`), the title as the notification's title,
  the priority, the registration's icon, and the url as what a tap opens (`X-Click`).
- **Telegram** gets the message as Telegram HTML, parsed as CommonMark plus `~~strikethrough~~`:
  - bold, italic, strikethrough, inline code, code blocks, links and blockquotes keep their form;
  - a heading is a bold line, a list item a `•` line, or a numbered one in an ordered list;
  - an image is a link to it, under its alt text, and a thematic break is `———`;
  - HTML in the message shows as text;
  - the first line is the mark and the title in bold; with no title, the mark starts the message;
  - link previews are off;
  - a message over Telegram's limit of 4096 characters, as shown, is cut and ends in `…`;
  - the url is one button under the message, `Open`.

Each submit shows the url it carries, and only that one: an update without a url leaves the ntfy
notification no click and takes the Open button off the Telegram message. The *Superseded.* and
*Dismissed.* edits carry no button. When Telegram refuses a send or an edit that carries the button
with a `400`, other than for a message that is unchanged or gone, the bridge makes that call once
more without it, and the chat's result is that second call's: the message lands without Open, and
the ntfy click is unaffected. So a url Telegram does not take for a button costs the button, not
the message, and the refusal is logged (see [Observability](#observability)).

ntfy keeps a message as notification text only while it is under ntfy's message size limit, 4096
bytes of UTF-8 by default. The bridge cuts the message under that limit, on a character boundary,
ending in `…`, so a message near 4096 characters, or a shorter one in a non-Latin script, still
arrives as text. A cut inside a Markdown construct leaves it open, and ntfy shows it as text.

### Observability

- `/metrics` exposes `notifications_total{registration,channel,outcome}`, each submit and dismiss
  counted per channel, with `outcome` `ok` or `error`, and `notifications_open_rows{registration,
  channel}`, the rows of state the bridge holds: one per sequence on ntfy, one per sequence and chat
  on Telegram.
- Each submit and dismiss logs one JSON line to stderr: the registration, the sequence id and each
  channel's result, or for a refused one, its status and why.
- A refused Open button logs one more, at `warning`: `"event": "button refused"`, with the
  `registration`, the `sequence_id` (`null` for a one-shot), the `chat` and Telegram's refusal as
  `error`, quoted as the result quotes it.

## Layout

A uv workspace: one member directory per deployable app, each its own uv project with its own
Dockerfile. The root `pyproject.toml` holds the one ruff and pytest configuration, and the
workspace's `uv.lock` sits beside it.

- `bridge/`: the notification bridge, the package `notification_bridge`, its tests and its
  `Dockerfile`.
- `Jenkinsfile` and `run-suite`: CI. On each push to `main`, Jenkins runs the suite through
  `run-suite`, builds the bridge's image, `registry:5000/notification-bridge`, and pins its tag into
  NotificationsDeploy.
- `docs/bridge-configuration.md`: the bridge's environment, registrations file, state and image.
- `docs/architecture/architecture.yaml`: the bridge's architecture model, the product, the API it
  exposes and what it consumes.

## Development

`kc project setup` syncs the workspace into `.venv`; `kc project lint` runs ruff's check and format
check over the whole tree, and `kc project test` runs the pytest suite over the whole tree, with
fakes in place of ntfy and Telegram.
`cexec aac-tools arch-validate docs/architecture/architecture.yaml` validates the architecture model.
That the bridge's edge to ntfy resolves is proved by NotificationsDeploy's `kc project test`, which
reads this file from a checkout beside this one.
