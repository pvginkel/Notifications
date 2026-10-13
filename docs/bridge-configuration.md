# The bridge's configuration

What the notification bridge reads when it starts, and what it needs from the container it runs in.
It reads its environment and its registrations once, at start: a change to either takes a restart.
The API it serves, with its rendering and its metrics, is in the
[README](../README.md#the-notification-bridge).

## The environment

| Variable | | Meaning |
|---|---|---|
| `NTFY_URL` | required | The ntfy server's URL, such as `http://127.0.0.1:8080`. The bridge publishes to `/<topic>` under it |
| `NTFY_TOKEN` | required | The ntfy access token the bridge sends as `Authorization: Bearer` on every call. Its user must be allowed to write to every registration's topic, where the bridge publishes and clears |
| `ICON_BASE_URL` | required | Where the phone fetches the icons: a notification's icon is `<ICON_BASE_URL>/<icon>`, the file name URL-quoted. It takes no trailing slash |
| `BRIDGE_PORT` | default `8090` | The port of the API and `/metrics`, served on every interface |
| `BRIDGE_REGISTRATIONS_FILE` | default `/etc/notification-bridge/registrations.yaml` | [The registrations file](#the-registrations-file) |
| `BRIDGE_ICONS_DIR` | default `/icons` | The directory each registration's icon is a file in |
| `BRIDGE_STATE_DIR` | default `/var/lib/notification-bridge` | [The state directory](#the-state-directory) |

Beside these, the environment holds the variables the registrations' `$NAME` placeholders name.
The `BRIDGE_*` variables let tests and a local run point the bridge at their own files; the image
sets none of them. Telegram is reached at `https://api.telegram.org`, which no variable changes.

## The registrations file

YAML with one key, `registrations`: a mapping from each registration's name to its token and its
channels. The example is invented:

```yaml
registrations:
  ci:
    token: $CI_TOKEN
    ntfy:
      topic: ops
      icon: ci.png
    telegram:
      botToken: $CI_TELEGRAM_BOT_TOKEN
      chatIds: $CI_TELEGRAM_CHAT_IDS
  alerts:
    token: $ALERTS_TOKEN
    ntfy:
      topic: ops
      icon: alerts.png
```

| Key | | Value |
|---|---|---|
| `token` | required | The bearer token the registration's senders hold. No two registrations hold the same one |
| `ntfy.topic` | with `ntfy` | The ntfy topic: 1 to 64 of `A`–`Z`, `a`–`z`, `0`–`9`, `-` and `_`. Registrations may share a topic |
| `ntfy.icon` | with `ntfy` | A file name, of a file directly in the icons directory |
| `telegram.botToken` | with `telegram` | The token of the registration's own bot |
| `telegram.chatIds` | with `telegram` | The chats, as one string of chat ids separated by commas. Spaces around an id are dropped |

- A registration has `ntfy`, `telegram` or both, and those are its channels.
- Its name is at most 64 bytes of UTF-8, because it keys the hash of the registration's ntfy ids.
  It also keys the registration's state, metrics and log lines: a renamed registration has lost its
  open sequences, as the 90-day prune loses them (the README's
  [Sequence ids](../README.md#sequence-ids)).
- `token` and every key under `ntfy` and `telegram` take a string. A YAML number is refused, so a
  single chat id written as a literal is quoted (`"-1001234567890"`).
- A value that is a whole `$NAME` placeholder (`$`, then letters, digits and `_`, not starting with
  a digit) is replaced by the environment variable `NAME`. Only a whole value is: `${NAME}`, or
  `$NAME` inside a longer string, is taken as it stands.

## Refusals

The bridge refuses to start rather than serve part of its registrations. It exits `1` and prints
`notification-bridge refuses to start: <why>` on stderr, the why naming what is wrong and where,
when:

- `NTFY_URL`, `NTFY_TOKEN` or `ICON_BASE_URL` is unset or empty, or `BRIDGE_PORT` is not a number;
- the registrations file cannot be read, is not YAML, or holds anything but one non-empty
  `registrations` mapping;
- a registration's name is empty or over 64 bytes, or it has neither channel;
- a registration, its `ntfy` or its `telegram` is not a mapping, holds a key the table does not
  list, or lacks one it requires;
- a value is not a string, or is empty, also once its placeholder is replaced;
- a placeholder names a variable that is not set;
- a topic is not a valid ntfy topic name, or an icon is not a file in the icons directory;
- `chatIds` holds an empty entry, or lists a chat twice;
- two registrations hold the same token.

## The state directory

The bridge keeps its state in `state.db`, an SQLite file it creates in the state directory. The
directory must exist and be writable by the bridge's user: the bridge does not create it, and when
it is missing, the start fails with a traceback ending `sqlite3.OperationalError: unable to open
database file`.

The file holds one table, of what an update and a dismiss need and never a title or a text: per open
sequence, a row for its ntfy notification (its priority and the generation it is published under)
and one for its message in each Telegram chat (its priority and the message id), each with when it
was last written. At
start and every 24 hours after, the bridge deletes the rows last written more than 90 days ago, and
logs `"event": "prune"` with the number `removed`.

A bridge that starts without the file has forgotten every open sequence, with the consequences the
README's [Sequence ids](../README.md#sequence-ids) gives for the prune, so the directory belongs on
a volume that outlives the container. Run one bridge per state file: the turns that submits and
dismisses of one sequence take are held in the bridge's process.

## The image

`registry:5000/notification-bridge`, built by the repo's `Jenkinsfile` on each push to `main` and
tagged with the build number and `latest`. It runs `notification-bridge` under `tini` as the user
`bridge`, uid 10001 and gid 10001, and exposes port 8090.

In the image, `/var/lib/notification-bridge` belongs to that user. A volume mounted there replaces
it, so the volume has to give uid 10001 or gid 10001 write access. The registrations file and the
icons directory are only read.
