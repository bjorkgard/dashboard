from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmWarpDisconnect(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel"), ("q", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="warp-confirm"):
            yield Static(
                "Disconnect Warp?\n\nThis immediately disables Warp collection and removes only "
                "Dayboard-owned current/retired API keys from Keychain. Other providers and "
                "Oz/Warp's own credentials are unchanged. Unsaved form edits are discarded.",
                markup=False,
            )
            with Horizontal():
                yield Button("Disconnect", id="warp-confirm-yes", variant="error")
                yield Button("Cancel", id="warp-confirm-no")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_mount(self) -> None:
        self.query_one("#warp-confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "warp-confirm-yes")
