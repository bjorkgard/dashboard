import asyncio
from dataclasses import replace

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from dayboard.config import Config, ConfigError, ConfigStore


class WelcomeScreen(Screen[Config]):
    BINDINGS = [("q", "app.quit", "Quit")]

    def __init__(self, store: ConfigStore, config: Config) -> None:
        super().__init__()
        self.store = store
        self.config = config

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="welcome-scroll"):
            with Vertical(id="welcome"):
                yield Static("DAYBOARD / FIRST RUN", id="welcome-title", markup=False)
                yield Static(
                    "Built on milestone 1: the secure foundation.\n"
                    "Local CPU/RAM/network are live.\n"
                    "Choose Configure for Calendar, weather and GitHub.\n"
                    "Optional integrations stay disabled until configured.\n\n"
                    "Continue without cloud accounts, or quit without saving.\n"
                    "Demo: uv run dayboard --demo",
                    id="welcome-copy",
                    markup=False,
                )
        yield Static("", id="setup-error", markup=False)
        with Horizontal(id="welcome-actions"):
            yield Button("Configure", id="configure", variant="primary")
            yield Button("Continue", id="continue")
            yield Button("Quit", id="quit")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "configure":
            event.stop()
            await self.app.run_action("settings")
            return
        if event.button.id == "quit":
            await self.app.action_quit()
        elif event.button.id == "continue":
            event.button.disabled = True
            config = replace(self.config, onboarding_complete=True)
            try:
                await asyncio.to_thread(self.store.save, config)
            except ConfigError:
                self.query_one("#setup-error", Static).update(
                    "Cannot save protected configuration. "
                    "Check directory ownership and permissions."
                )
                event.button.disabled = False
                return
            self.dismiss(config)
