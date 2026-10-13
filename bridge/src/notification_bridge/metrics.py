"""The bridge's Prometheus metrics: what each channel did, and the rows the state holds."""

from collections.abc import Iterable, Iterator

from prometheus_client import CollectorRegistry, Counter
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from notification_bridge.state import State

# The counter's outcome label: never a refusal's text, so its values stay bounded.
OUTCOMES = ("ok", "error")


class Metrics:
    """The metrics of a set of (registration, channel) pairs, each of whose series starts at 0."""

    def __init__(self, state: State, pairs: Iterable[tuple[str, str]]) -> None:
        pairs = tuple(pairs)
        self.registry = CollectorRegistry()
        self._notifications = Counter(
            "notifications",
            "Submits and dismisses by registration, channel and outcome",
            ["registration", "channel", "outcome"],
            registry=self.registry,
        )
        # A series that first appears at 1 has no earlier sample for rate() to rise from.
        for registration, channel in pairs:
            for outcome in OUTCOMES:
                self._notifications.labels(registration, channel, outcome)
        self.registry.register(_OpenRows(state, pairs))

    def count(self, registration: str, channel: str, ok: bool) -> None:
        self._notifications.labels(registration, channel, "ok" if ok else "error").inc()


class _OpenRows(Collector):
    """The state's rows per registration and channel, counted at each scrape."""

    def __init__(self, state: State, pairs: tuple[tuple[str, str], ...]) -> None:
        self._state = state
        self._pairs = pairs

    def collect(self) -> Iterator[GaugeMetricFamily]:
        gauge = GaugeMetricFamily(
            "notifications_open_rows",
            "Rows of open sequences in the state, by registration and channel",
            labels=["registration", "channel"],
        )
        counts = dict.fromkeys(self._pairs, 0) | self._state.open_rows()
        for (registration, channel), count in counts.items():
            gauge.add_metric([registration, channel], count)
        yield gauge
