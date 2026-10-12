# Notifications

The notification platform's apps. The first is the notification bridge: one API over ntfy and
Telegram. Producers submit and dismiss notifications through it; it delivers them to ntfy and
Telegram per registration.

Deployed by [NotificationsDeploy](https://github.com/pvginkel/NotificationsDeploy); its specs and
slices live in NotificationsSpecs.

## Layout

A uv workspace: one member directory per deployable app, each its own uv project with its own
Dockerfile. The root `pyproject.toml` holds the one ruff and pytest configuration and the workspace's
`uv.lock`. No app is built yet.

## Development

`kc project setup` syncs the workspace into `.venv`; `kc project lint` runs ruff's check and format
check over the whole tree, and `kc project test` will run the pytest suite once an app brings one.
