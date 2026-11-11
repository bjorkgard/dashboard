"""Milestone-one shell. Real integrations and full navigation are intentionally absent."""

from datetime import datetime
from importlib.metadata import version

from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.widgets import Footer, Static

from dayboard.config import Config, ConfigStore
from dayboard.core.models import ProviderKey
from dayboard.core.providers import Capability, ProviderRegistry
from dayboard.core.scheduler import Scheduler
from dayboard.core.store import SnapshotStore
from dayboard.paths import AppPaths
from dayboard.ui.card import SnapshotCard
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
    BINDINGS = [("q", "quit", "Quit"), ("question_mark", "help", "Help")]

    def __init__(self, *, demo: bool = False, paths: AppPaths | None = None) -> None:
        super().__init__()
        self.demo = demo
        # Demo deliberately does not resolve, load, create, or save real application state.
        self.config_store = None if demo else ConfigStore(paths or AppPaths.default())
        self.config = Config() if demo else (self.config_store.load() or Config())  # type: ignore[union-attr]
        if demo:
            from dayboard.demo import demo_registry

            self.registry = demo_registry()
        else:
            self.registry = ProviderRegistry()
        self.store = SnapshotStore()
        self.scheduler = Scheduler(self.registry, self.store)

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("DAYBOARD", id="brand", markup=False)
            yield Static(f"FOUNDATION / {version('dayboard')}", id="release", markup=False)
        yield Static(
            "DEMO · SYNTHETIC DATA · No accounts, network, or state access"
            if self.demo
            else "LOCAL WORKSPACE · No services connected",
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
                    yield SnapshotCard(panel, name)
            yield Static(
                "APPLE NEWS  /  Feed connection and clickable ticker arrive in milestone 4.",
                id="news-notice",
                markup=False,
            )
            yield Static(
                "Milestone 1 · Secure foundations only. Tabs, flip-clock animation and provider "
                "setup are not implemented yet.",
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

    def _refresh_view(self) -> None:
        # Query the base screen even while onboarding is pushed above it.
        base = self.screen_stack[0]
        now = datetime.now().astimezone()
        base.query_one("#clock", Static).update(
            now.strftime("%H:%M:%S") + "\n" + now.strftime("%A, %B %d  ·  %Z")
        )
        if self.demo:
            for panel, _, _ in PANELS:
                snapshot = self.store.status(ProviderKey(f"demo-{panel}")).snapshot
                base.query_one(f"#card-{panel}", SnapshotCard).show_snapshot(snapshot)

    async def action_quit(self) -> None:
        await self.scheduler.close()
        self.exit()

    def action_help(self) -> None:
        self.notify(
            "q: Quit · Tab: Focus · Arrows/Page Down: Scroll · "
            "uv run dayboard doctor: Safe diagnostics. Full tabs arrive in milestone 2.",
            title="Dayboard / Foundation",
        )

    async def on_unmount(self) -> None:
        await self.scheduler.close()
