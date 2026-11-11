"""Keyboard-first integration preferences; explicit search/authorization, atomic Save."""

import asyncio
import math
from collections.abc import Coroutine
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Select, Static, Switch
from textual.worker import Worker

from dayboard.calendar_setup import authorize_calendar, calendar_status
from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.core.providers import RateLimited
from dayboard.integrations.geocoding import Geocoder, LocationChoice
from dayboard.paths import AppPaths
from dayboard.security.redaction import safe_text
from dayboard.settings_editor import (
    SettingsDraft,
    SettingsValidationError,
    build_candidate,
    save_draft,
)


class SettingsScreen(Screen[Config | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        config: Config,
        store: ConfigStore | None,
        paths: AppPaths | None,
        *,
        demo: bool = False,
        connections: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.paths = paths
        self.demo = demo
        self.connections = connections or {}
        self.geocoder = Geocoder()
        self._owned: dict[str, Worker[None]] = {}
        self._choices: tuple[LocationChoice, ...] = ()
        self._search_generation = 0
        self._calendar_generation = 0
        self._calendar_state = "unknown"
        self._authorizing = False
        self._saving = False

    def compose(self) -> ComposeResult:
        disabled = self.demo
        weather, calendar, github = self.config.weather, self.config.calendar, self.config.github
        location_saved = bool(weather.label)
        yield Static("INTEGRATION SETTINGS", id="settings-title", markup=False)
        yield Static(
            "DEMO · Read-only preview; no state, search or permission access."
            if self.demo
            else "Changes apply only on Save. Cancel leaves running sources unchanged.",
            id="settings-hint",
            markup=False,
        )
        yield Static("", id="settings-message", markup=False)
        with VerticalScroll(id="settings-form"):
            with Vertical(classes="settings-panel"):
                yield Static("WEATHER", classes="settings-section", markup=False)
                yield Static(
                    f"Saved: {'on' if weather.enabled else 'off'} · "
                    f"Current: {self.connections.get('weather', 'not connected')}",
                    classes="settings-note",
                    markup=False,
                )
                with Horizontal(classes="settings-toggle"):
                    yield Static("Enable weather", markup=False)
                    yield Switch(weather.enabled, id="weather-enabled", disabled=disabled)
                yield Static(
                    "Search city / postal code (sent only after Search)", classes="settings-label"
                )
                yield Input(
                    placeholder="City, country or region", id="location-query", disabled=disabled
                )
                yield Button("Search locations", id="location-search", disabled=disabled)
                yield Select(
                    [], prompt="Select a matching location", id="location-choice", disabled=True
                )
                yield Static("", id="location-status", classes="settings-note", markup=False)
                yield Static("Location display name", classes="settings-label")
                yield Input(weather.label, id="weather-name", disabled=disabled)
                with Horizontal(classes="settings-pair"):
                    with Vertical():
                        yield Static("Latitude", classes="settings-label")
                        yield Input(
                            f"{weather.latitude:g}" if location_saved else "",
                            id="weather-latitude",
                            disabled=disabled,
                        )
                    with Vertical():
                        yield Static("Longitude", classes="settings-label")
                        yield Input(
                            f"{weather.longitude:g}" if location_saved else "",
                            id="weather-longitude",
                            disabled=disabled,
                        )
                yield Static("IANA timezone", classes="settings-label")
                yield Input(weather.timezone, id="weather-timezone", disabled=disabled)
                yield Select(
                    [("Celsius · km/h", "celsius"), ("Fahrenheit · mph", "fahrenheit")],
                    value=weather.temperature_unit,
                    allow_blank=False,
                    id="weather-units",
                    disabled=disabled,
                )
                yield Static(
                    "Manual coordinates are supported. Open-Meteo / GeoNames attribution; "
                    "public endpoints are for noncommercial use. No IP geolocation. "
                    "Switch on to save edits; disabling preserves the saved location.",
                    classes="settings-note",
                    markup=False,
                )
            with Vertical(classes="settings-panel"):
                yield Static("CALENDAR", classes="settings-section", markup=False)
                yield Static(
                    f"Saved: {'on' if calendar.enabled else 'off'} · "
                    f"Current: {self.connections.get('calendar', 'not connected')}",
                    classes="settings-note",
                    markup=False,
                )
                with Horizontal(classes="settings-toggle"):
                    yield Static("Enable Calendar collection", markup=False)
                    yield Switch(calendar.enabled, id="calendar-enabled", disabled=disabled)
                yield Static(
                    "Permission status is not checked in demo."
                    if self.demo
                    else "Checking helper status…",
                    id="calendar-status",
                    classes="settings-note",
                    markup=False,
                )
                yield Button("Authorize Calendar", id="calendar-authorize", disabled=disabled)
                yield Static(
                    "Only Authorize may build the helper and request macOS access. "
                    "The OS grant is separate from Save. Existing calendar filters are preserved. "
                    "Disabling collection does not revoke the OS grant.",
                    classes="settings-note",
                    markup=False,
                )
            with Vertical(classes="settings-panel"):
                yield Static("GITHUB", classes="settings-section", markup=False)
                yield Static(
                    f"Saved: {'on' if github.enabled else 'off'} · "
                    f"Current: {self.connections.get('github', 'not connected')}",
                    classes="settings-note",
                    markup=False,
                )
                with Horizontal(classes="settings-toggle"):
                    yield Static("Enable GitHub status", markup=False)
                    yield Switch(github.enabled, id="github-enabled", disabled=disabled)
                yield Static(
                    "1–5 OWNER/REPO selections, separated by commas or spaces",
                    classes="settings-label",
                )
                yield Input(", ".join(github.repositories), id="github-repos", disabled=disabled)
                yield Static(
                    "github.com only. Authentication remains with GitHub CLI. If needed, "
                    "run gh auth login --hostname github.com separately. No tokens/passwords here. "
                    "Switch on to save edits; disabling preserves saved repositories.",
                    classes="settings-note",
                    markup=False,
                )
        with Horizontal(id="settings-actions"):
            yield Button("Save [Ctrl-S]", id="settings-save", variant="primary", disabled=disabled)
            yield Button("Cancel [Esc]", id="settings-cancel")
        yield Footer()

    def _launch(self, coroutine: Coroutine[Any, Any, None], group: str) -> None:
        previous = self._owned.get(group)
        if previous is not None:
            previous.cancel()
        self._owned[group] = self.run_worker(
            coroutine,
            name=group,
            group=group,
            exclusive=True,
            exit_on_error=False,
        )

    def _cancel_work(self) -> None:
        for worker in self._owned.values():
            worker.cancel()
        self._owned.clear()

    def on_mount(self) -> None:
        if not self.demo and self.paths is not None:
            self._launch(self._inspect_calendar(self._calendar_generation), "calendar-status")

    async def _inspect_calendar(self, generation: int) -> None:
        assert self.paths is not None
        try:
            result = await calendar_status(self.paths)
            state = result.get("status", "unavailable")
        except Exception:
            state = "unavailable"
        if self.is_mounted and generation == self._calendar_generation:
            self._calendar_state = str(state)
            labels = {
                "not_built": "Helper not built. Authorize explicitly to build and request access.",
                "not_determined": "Calendar permission not granted yet.",
                "authorized": "OS permission authorized. Enable collection and Save to apply.",
                "denied": "Calendar access denied. Review the terminal/host's macOS privacy grant.",
                "restricted": "Calendar access restricted by macOS.",
            }
            self.query_one("#calendar-status", Static).update(
                labels.get(
                    self._calendar_state, "Helper unavailable; check macOS/Swift requirements."
                )
            )

    def _message(self, text: str) -> None:
        self.query_one("#settings-message", Static).update(safe_text(text))

    def _draft(self) -> SettingsDraft:
        units = self.query_one("#weather-units", Select).value
        return SettingsDraft(
            calendar_enabled=self.query_one("#calendar-enabled", Switch).value,
            weather_enabled=self.query_one("#weather-enabled", Switch).value,
            weather_label=self.query_one("#weather-name", Input).value,
            latitude=self.query_one("#weather-latitude", Input).value,
            longitude=self.query_one("#weather-longitude", Input).value,
            timezone=self.query_one("#weather-timezone", Input).value,
            units=units if isinstance(units, str) else "",
            github_enabled=self.query_one("#github-enabled", Switch).value,
            repositories=self.query_one("#github-repos", Input).value,
        )

    def _lock_controls(self, locked: bool) -> None:
        for control in self.query("Input, Switch, Select, Button"):
            control.disabled = locked or self.demo
        self.query_one("#settings-cancel", Button).disabled = locked
        if not locked:
            self.query_one("#location-choice", Select).disabled = self.demo or not self._choices

    def action_save(self) -> None:
        if self.demo or self._saving or self.store is None:
            return
        if self._authorizing:
            self._message("Calendar authorization is still in progress. Wait or Cancel.")
            return
        draft = self._draft()
        try:
            build_candidate(self.config, draft)
            if (
                draft.calendar_enabled
                and not self.config.calendar.enabled
                and self._calendar_state != "authorized"
            ):
                raise SettingsValidationError("Calendar", "Authorize Calendar before enabling it.")
        except SettingsValidationError as error:
            self._message(str(error))
            return
        self._saving = True
        self._lock_controls(True)
        self._message("Saving and applying settings…")
        pending = self._owned.get("location-search")
        if pending is not None:
            pending.cancel()
        self._launch(self._save(draft), "settings-save")

    async def _save(self, draft: SettingsDraft) -> None:
        assert self.store is not None
        try:
            config = await asyncio.to_thread(save_draft, self.store, self.config, draft)
        except Exception as error:
            message = (
                "Integration settings changed externally. Cancel and reopen Settings."
                if isinstance(error, ConfigError) and "changed externally" in str(error)
                else "Cannot save protected settings. Check application directory permissions."
            )
            self._message(message)
            self._saving = False
            self._lock_controls(False)
            return
        self.dismiss(config)

    def action_cancel(self) -> None:
        if self._saving:
            return
        self._cancel_work()
        self.dismiss(None)

    def _start_search(self) -> None:
        if self.demo or self._saving:
            return
        self._search_generation += 1
        self._choices = ()
        select = self.query_one("#location-choice", Select)
        select.clear()
        select.set_options([])
        select.disabled = True
        self.query_one("#location-status", Static).update("Searching…")
        query = self.query_one("#location-query", Input).value
        self._launch(self._search(query, self._search_generation), "location-search")

    async def _search(self, query: str, generation: int) -> None:
        try:
            choices = await self.geocoder.search(query)
        except RateLimited as error:
            message = (
                f"Location search rate limited. Retry in {math.ceil(error.retry_after)} seconds."
            )
            choices = ()
        except Exception:
            message = (
                "Could not search locations. Check the city name/network, or enter coordinates."
            )
            choices = ()
        else:
            message = (
                "Select a matching city; no result is chosen automatically."
                if choices
                else "No locations found."
            )
        if self.is_mounted and not self._saving and generation == self._search_generation:
            self._choices = choices
            select = self.query_one("#location-choice", Select)
            select.set_options(
                [(Text(choice.label), index) for index, choice in enumerate(choices)]
            )
            select.clear()
            select.disabled = not choices
            self.query_one("#location-status", Static).update(message)

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            event.select.id != "location-choice"
            or self.demo
            or self._saving
            or type(event.value) is not int
            or event.select.value != event.value
            or not 0 <= event.value < len(self._choices)
        ):
            return
        choice = self._choices[event.value]
        self.query_one("#weather-name", Input).value = choice.label
        self.query_one("#weather-latitude", Input).value = f"{choice.latitude:g}"
        self.query_one("#weather-longitude", Input).value = f"{choice.longitude:g}"
        self.query_one("#weather-timezone", Input).value = choice.timezone
        self.query_one("#weather-enabled", Switch).value = True
        self.query_one("#location-status", Static).update(
            "Location selected. Save to enable weather."
        )

    def _start_authorize(self) -> None:
        if self.demo or self._saving or self._authorizing or self.paths is None:
            return
        self._calendar_generation += 1
        pending = self._owned.get("calendar-status")
        if pending is not None:
            pending.cancel()
        self._authorizing = True
        self.query_one("#calendar-authorize", Button).disabled = True
        self.query_one("#calendar-status", Static).update(
            "Building helper/requesting macOS permission… Cancel stops this operation."
        )
        self._launch(self._authorize(), "calendar-authorize")

    async def _authorize(self) -> None:
        assert self.paths is not None
        try:
            result = await authorize_calendar(self.paths)
            state = result.get("status")
            self._calendar_state = "authorized" if state == "authorized" else "denied"
            if self.is_mounted:
                self.query_one("#calendar-enabled", Switch).value = state == "authorized"
                self.query_one("#calendar-status", Static).update(
                    "OS permission authorized. Save to enable collection."
                    if state == "authorized"
                    else "Access not granted. Review macOS Calendar privacy "
                    "permissions for your host."
                )
        except Exception:
            if self.is_mounted:
                self._calendar_state = "unavailable"
                self.query_one("#calendar-status", Static).update(
                    "Authorization unavailable/timed out. Requires macOS 14+, "
                    "Swift tools and a supported host."
                )
        finally:
            self._authorizing = False
            if self.is_mounted:
                self.query_one("#calendar-authorize", Button).disabled = self.demo or self._saving

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "settings-save": self.action_save,
            "settings-cancel": self.action_cancel,
            "location-search": self._start_search,
            "calendar-authorize": self._start_authorize,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            event.stop()
            action()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "location-query":
            self._start_search()

    def on_unmount(self) -> None:
        self._cancel_work()
