"""Local-first overview with explicitly enabled calendar, weather and GitHub sources."""

from datetime import datetime
from importlib.metadata import version

from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Static

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.models import ProviderKey
from dayboard.core.providers import Capability, ProviderRegistry
from dayboard.core.scheduler import Scheduler
from dayboard.core.store import SnapshotStore
from dayboard.integrations.runtime import build_live_registry
from dayboard.paths import AppPaths
from dayboard.ui.card import SnapshotCard
from dayboard.ui.settings import SettingsScreen
from dayboard.ui.telemetry import TelemetryCard
from dayboard.ui.welcome import WelcomeScreen

PANELS: tuple[tuple[str, str, Capability], ...] = (
    ("mac", "MAC PULSE", Capability.SYSTEM),
    ("schedule", "TODAY", Capability.CALENDAR),
    ("inbox", "INBOX", Capability.MAIL),
    ("github", "GITHUB ATTENTION", Capability.GITHUB),
    ("ai", "AI ACTIVITY", Capability.AI),
    ("weather", "OUTSIDE", Capability.WEATHER),
)


class DayboardApp(App[None]):
    TITLE = "Dayboard"
    CSS_PATH = "ui/styles/shell.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "settings", "Settings"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(self, *, demo: bool = False, paths: AppPaths | None = None) -> None:
        super().__init__()
        self.demo = demo
        # Demo deliberately does not resolve, load, create, or save real application state.
        self.config_store: ConfigStore | None = None
        self.paths: AppPaths | None = None
        self._applying_settings = False
        self.config = Config()
        if demo:
            from dayboard.demo import demo_registry

            self.registry = demo_registry()
        else:
            paths = paths or AppPaths.default()
            self.paths = paths
            self.config_store = ConfigStore(paths)
            self.config = self.config_store.load() or Config()
            self.registry = build_live_registry(self.config, paths)
        self.store = SnapshotStore()
        self.scheduler = Scheduler(self.registry, self.store, on_change=self._refresh_view)

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("DAYBOARD", id="brand", markup=False)
            yield Static(f"FOUNDATION / {version('dayboard')}", id="release", markup=False)
            yield Button("Settings [s]", id="settings-open")
        yield Static(
            "DEMO · SYNTHETIC DATA · No accounts, network, or state access"
            if self.demo
            else f"LOCAL WORKSPACE · {len(self.registry.all())} enabled sources",
            id="mode",
            markup=False,
        )
        with VerticalScroll(id="workspace"):
            yield Static("", id="clock", markup=False)
            yield Static(
                "AT A GLANCE   /   Foundation preview"
                if self.demo
                else "AT A GLANCE   /   Ready for your future integrations",
                id="section-title",
                markup=False,
            )
            with Grid(id="cards"):
                for panel, name, _ in PANELS:
                    if panel == "mac" and not self.demo:
                        yield TelemetryCard(panel, name)
                    else:
                        yield SnapshotCard(panel, name)
            yield Static(
                "APPLE NEWS  /  Feed connection and clickable ticker remain on the roadmap.",
                id="news-notice",
                markup=False,
            )
            yield Static(
                "Local CPU/RAM/network are live. Calendar/weather/GitHub are opt-in. "
                "Full tabs, mail and "
                "flip-clock animation remain on the roadmap.",
                id="scope",
                markup=False,
            )
        yield Footer()

    async def on_mount(self) -> None:
        if self.demo:
            # These providers are in-memory only; seed the view before its first frame.
            for provider in self.registry.all():
                await self.scheduler.refresh(provider.spec.key)
        self.scheduler.start()
        self._refresh_view()
        self.set_interval(1, self._refresh_view)
        if not self.demo and not self.config.onboarding_complete and self.config_store is not None:
            self.push_screen(WelcomeScreen(self.config_store, self.config), self._setup_finished)

    def _setup_finished(self, config: Config | None) -> None:
        if config is not None:
            self.config = config

    def action_settings(self) -> None:
        if self._applying_settings or isinstance(self.screen, SettingsScreen):
            return
        config = self.config
        if not self.demo and self.config_store is not None:
            try:
                config = self.config_store.load() or Config()
            except ConfigError:
                self.notify(
                    "Cannot open Settings: saved configuration is invalid or inaccessible.",
                    severity="error",
                )
                return
        self.push_screen(
            SettingsScreen(
                config,
                self.config_store,
                self.paths,
                demo=self.demo,
                connections={} if self.demo else self._connection_summary(),
            ),
            self._settings_finished,
        )

    def _connection_summary(self) -> dict[str, str]:
        result = {}
        for name, capability in (
            ("weather", Capability.WEATHER),
            ("calendar", Capability.CALENDAR),
            ("github", Capability.GITHUB),
        ):
            provider = next(
                (p for p in self.registry.all() if capability in p.spec.capabilities),
                None,
            )
            if provider is None:
                result[name] = "not connected"
            else:
                status = self.store.status(
                    provider.spec.key, stale_after=provider.spec.interval_seconds * 2
                )
                result[name] = status.state.value + (
                    f" · {status.error.value}" if status.error else ""
                )
        return result

    async def _settings_finished(self, config: Config | None) -> None:
        if config is None or self.demo or self.paths is None:
            return
        self._applying_settings = True
        try:
            if isinstance(self.screen, WelcomeScreen):
                self.screen.dismiss(config)
            await self.scheduler.close()
            self.config = config
            self.store = SnapshotStore()
            started = True
            try:
                self.registry = build_live_registry(config, self.paths)
            except Exception:
                started = False
                self.registry = ProviderRegistry()
                self.notify(
                    "Preferences saved, but sources could not start. "
                    "Collectors stopped; restart Dayboard.",
                    severity="error",
                )
            self.scheduler = Scheduler(self.registry, self.store, on_change=self._refresh_view)
            for card in self.screen_stack[0].query(SnapshotCard):
                card.show_snapshot(None)
            self.scheduler.start()
            self._refresh_view()
            if started:
                self.notify(
                    "Settings applied. Sources are warming up; history was reset.", title="Settings"
                )
        finally:
            self._applying_settings = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-open":
            event.stop()
            self.action_settings()

    def _refresh_view(self) -> None:
        # Query the base screen even while onboarding is pushed above it.
        base = self.screen_stack[0]
        if not self.demo:
            base.query_one("#mode", Static).update(
                f"LOCAL WORKSPACE · {len(self.registry.all())} enabled sources"
            )
        now = datetime.now().astimezone()
        base.query_one("#clock", Static).update(
            now.strftime("%H:%M:%S") + "\n" + now.strftime("%A, %B %d  ·  %Z")
        )
        for panel, _, capability in PANELS:
            card = base.query_one(f"#card-{panel}", SnapshotCard)
            if self.demo:
                snapshot = self.store.status(ProviderKey(f"demo-{panel}")).snapshot
                card.show_snapshot(snapshot)
                continue
            provider = next(
                (p for p in self.registry.all() if capability in p.spec.capabilities),
                None,
            )
            if provider is not None:
                status = self.store.status(
                    provider.spec.key, stale_after=provider.spec.interval_seconds * 2
                )
                if isinstance(card, TelemetryCard):
                    card.show_telemetry(status, self.store.history(provider.spec.key))
                else:
                    card.show_snapshot(
                        status.snapshot,
                        state=status.state.value,
                        error=status.error,
                        loading=status.attempted_at is None,
                    )

    async def action_quit(self) -> None:
        await self.scheduler.close()
        self.exit()

    def action_help(self) -> None:
        self.notify(
            "q: Quit · s: Settings · Tab: Focus · Arrows/Page Down: Scroll · "
            "Ctrl-S: Save / Esc: Cancel in Settings · "
            "uv run dayboard doctor: Safe diagnostics. Full workspace tabs remain planned.",
            title="Dayboard / Foundation",
        )

    async def on_unmount(self) -> None:
        await self.scheduler.close()
