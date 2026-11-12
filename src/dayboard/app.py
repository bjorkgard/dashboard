"""Tabbed local-first workspace with shared, explicitly configured collectors."""

from datetime import datetime
from importlib.metadata import version

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.events import Resize
from textual.widgets import Button, Footer, Static, TabbedContent, TabPane

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.models import ConnectionState, ProviderKey, SourceStatus
from dayboard.core.providers import Capability, ProviderRegistry
from dayboard.core.scheduler import Scheduler
from dayboard.core.store import SnapshotStore
from dayboard.integrations.runtime import build_live_registry
from dayboard.paths import AppPaths
from dayboard.ui.ai import AICard, AIDetailCard
from dayboard.ui.card import SnapshotCard
from dayboard.ui.details import DetailCard
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
        Binding("q", "quit", "Quit"),
        Binding("s", "settings", "Settings"),
        Binding("question_mark", "help", "Help"),
        Binding("1", "workspace('overview')", "Overview", show=False),
        Binding("2", "workspace('schedule')", "Schedule", show=False),
        Binding("3", "workspace('mac')", "Mac", show=False),
        Binding("4", "workspace('github')", "GitHub", show=False),
        Binding("5", "workspace('ai')", "AI", show=False),
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
            yield Static(f"WORKSPACE / {version('dayboard')}", id="release", markup=False)
            yield Button("Settings [s]", id="settings-open")
        yield Static(
            "DEMO · SYNTHETIC DATA · No accounts, network, or state access"
            if self.demo
            else f"LOCAL WORKSPACE · {len(self.registry.all())} enabled sources",
            id="mode",
            markup=False,
        )
        with TabbedContent(initial="overview", id="workspace-tabs"):
            with TabPane("1 Overview", id="overview"):
                with VerticalScroll(id="workspace", classes="workspace-scroll"):
                    yield Static("", id="clock", markup=False)
                    yield Static(
                        "AT A GLANCE   /   Synthetic preview" if self.demo else "AT A GLANCE",
                        id="section-title",
                        markup=False,
                    )
                    with Grid(id="cards"):
                        for panel, name, _ in PANELS:
                            if panel == "mac" and not self.demo:
                                yield TelemetryCard(panel, name)
                            elif panel == "ai":
                                yield AICard(panel, name)
                            else:
                                yield SnapshotCard(panel, name)
                    yield Static(
                        "APPLE NEWS  /  Feed connection and clickable ticker "
                        "remain on the roadmap.",
                        id="news-notice",
                        markup=False,
                    )
                    yield Static(
                        "Local CPU/RAM/network are live. Calendar/weather/GitHub are opt-in. "
                        "Claude local usage is opt-in. Mail, other AI integrations and "
                        "flip-clock animation remain on the roadmap.",
                        id="scope",
                        markup=False,
                    )
            with TabPane("2 Schedule", id="schedule"):
                with VerticalScroll(id="schedule-scroll", classes="workspace-scroll"):
                    yield Static(
                        "SCHEDULE / Next seven days · local timezone\n"
                        "Read-only macOS Calendar; up to 100 occurrences with source labels. "
                        "Reminders and additional calendar providers remain planned.",
                        classes="detail-note",
                        markup=False,
                    )
                    yield DetailCard("schedule-detail", "SEVEN-DAY AGENDA")
            with TabPane("3 Mac", id="mac"):
                with VerticalScroll(id="mac-scroll", classes="workspace-scroll"):
                    yield Static(
                        "MAC / CPU, memory and network\n"
                        "Shared bounded history; unknown samples are dots, not zeros. "
                        "Network totals may duplicate VPN/bridge traffic. "
                        "Disk, battery, processes and advanced sensors remain planned.",
                        classes="detail-note",
                        markup=False,
                    )
                    if self.demo:
                        yield DetailCard("mac-detail", "SYNTHETIC MAC METRICS")
                    else:
                        yield TelemetryCard("mac-detail", "RECENT CPU / RAM / NETWORK")
                        yield DetailCard("mac-metrics", "COLLECTED METRICS")
            with TabPane("4 GitHub", id="github"):
                with VerticalScroll(id="github-scroll", classes="workspace-scroll"):
                    yield Static(
                        "GITHUB / Selected repositories on github.com\n"
                        "Bounded read-only PR and default-branch commit lists, "
                        "not an account-wide activity feed. No extra requests on tab changes.",
                        classes="detail-note",
                        markup=False,
                    )
                    yield DetailCard("github-detail", "REPOSITORY ACTIVITY")
            with TabPane("5 AI", id="ai"):
                with VerticalScroll(id="ai-scroll", classes="workspace-scroll"):
                    yield Static(
                        "AI / Claude Code selected local source · partial account coverage\n"
                        "Today / 7 / 30 calendar days in the saved timezone. Source check time "
                        "is separate from latest usage activity. History is memory-only; "
                        "restart/Save reindexes. Costs and account quotas are unavailable.",
                        classes="detail-note",
                        markup=False,
                    )
                    yield AIDetailCard("ai-detail", "CLAUDE CODE USAGE")
        yield Footer()

    def action_workspace(self, pane: str) -> None:
        # Never navigate underneath Settings/onboarding or consume their text input.
        if self.screen is not self.screen_stack[0] or self._applying_settings:
            return
        if pane not in ("overview", "schedule", "mac", "github", "ai"):
            return
        self.query_one("#workspace-tabs", TabbedContent).active = pane
        scroll = "workspace" if pane == "overview" else f"{pane}-scroll"
        self.query_one(f"#{scroll}", VerticalScroll).focus()

    def on_resize(self, event: Resize) -> None:
        cards = self.screen_stack[0].query("#cards")
        if cards:
            cards.first(Grid).styles.grid_size_columns = 1 if event.size.width < 100 else 2

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
            ("claude", Capability.AI),
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
            cards = [base.query_one(f"#card-{panel}", SnapshotCard)]
            if panel in ("schedule", "mac", "github", "ai"):
                cards.append(base.query_one(f"#card-{panel}-detail", SnapshotCard))
            if panel == "mac" and not self.demo:
                cards.append(base.query_one("#card-mac-metrics", SnapshotCard))
            if self.demo:
                status = self.store.status(ProviderKey(f"demo-{panel}"))
                for card in cards:
                    card.show_snapshot(status.snapshot, state=status.state.value)
                continue
            provider = next(
                (p for p in self.registry.all() if capability in p.spec.capabilities),
                None,
            )
            status = (
                self.store.status(provider.spec.key, stale_after=provider.spec.interval_seconds * 2)
                if provider is not None
                else SourceStatus(ConnectionState.DISCONNECTED)
            )
            for card in cards:
                if isinstance(card, TelemetryCard) and provider is not None:
                    card.show_telemetry(status, self.store.history(provider.spec.key))
                else:
                    card.show_snapshot(
                        status.snapshot,
                        state=status.state.value,
                        error=status.error,
                        loading=provider is not None and status.attempted_at is None,
                    )

    async def action_quit(self) -> None:
        await self.scheduler.close()
        self.exit()

    def action_help(self) -> None:
        self.notify(
            "1–5: Overview / Schedule / Mac / GitHub / AI · q: Quit · s: Settings · "
            "Tab/Shift-Tab: Focus · Left/Right on tabs: Switch · Arrows/Page Down: Scroll · "
            "Ctrl-S: Save / Esc: Cancel in Settings · "
            "uv run dayboard doctor: Safe diagnostics.",
            title="Dayboard / Workspace",
        )

    async def on_unmount(self) -> None:
        await self.scheduler.close()
