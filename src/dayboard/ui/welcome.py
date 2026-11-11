import asyncio
from dataclasses import replace

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
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
                    "Your terminal workspace starts here.\n\n"
                    "This is milestone 1: the secure foundation.\n"
                    "Account selection and login arrive in later milestones.\n"
                    "No accounts, private files, or system permissions are accessed now.\n\n"
                    "Continue with an unconnected workspace, or quit without saving.\n"
                    "For an isolated sample, run: uv run dayboard --demo",
                    id="welcome-copy",
                    markup=False,
                )
                yield Static("", id="setup-error", markup=False)
                yield Button("Continue without accounts", id="continue", variant="primary")
                yield Button("Quit without saving", id="quit")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
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
