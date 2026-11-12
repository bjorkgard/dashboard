"""Stage app-owned keys without overwriting a working credential before config commits."""

import secrets
from dataclasses import replace

from dayboard.config import Config, ConfigError, ConfigStore
from dayboard.security.keychain import Keychain, KeychainUnavailable


class WarpCredentialError(ConfigError):
    pass


def save_with_warp_key(
    store: ConfigStore,
    candidate: Config,
    previous: Config,
    key: str,
    *,
    keychain: Keychain | None = None,
) -> Config:
    if not candidate.warp.enabled:
        store.save(candidate)
        return candidate
    if key and (len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise WarpCredentialError(
            "Enter a valid API key without whitespace; it is stored only in Keychain."
        )
    try:
        backend = keychain or Keychain()
        if not key:
            if not backend.get("warp", candidate.warp.credential_id):
                raise WarpCredentialError("Enter a Warp API key or configure it explicitly.")
            store.save(candidate)
            return candidate
        old_ids = previous.warp.retired_credential_ids
        if previous.warp.credential_id != "default" or previous.warp.enabled:
            old_ids = tuple(dict.fromkeys((*old_ids, previous.warp.credential_id)))
        if len(old_ids) > 8:
            raise WarpCredentialError(
                "Disconnect and remove old Dayboard keys before rotating again."
            )
        identifier = secrets.token_hex(16)
        staged = replace(
            candidate,
            warp=replace(candidate.warp, credential_id=identifier, retired_credential_ids=old_ids),
        )
        backend.set("warp", identifier, key)
        try:
            store.save(staged)
        except BaseException:
            try:
                committed = store.load()
            except ConfigError:
                raise WarpCredentialError(
                    "Save outcome uncertain; staged key preserved. Reopen Settings before retrying."
                ) from None
            if committed == staged:
                return staged
            if committed is not None and committed.warp.credential_id == identifier:
                raise WarpCredentialError(
                    "Settings changed while saving; referenced key preserved. Reopen Settings."
                ) from None
            try:
                backend.delete("warp", identifier)
            except KeychainUnavailable:
                raise WarpCredentialError(
                    "Preferences unchanged, but unused staged-key cleanup failed. "
                    "Review Dayboard Keychain entries."
                ) from None
            raise
        return staged
    except KeychainUnavailable:
        raise WarpCredentialError(
            "Cannot access macOS Keychain; no plaintext fallback is used."
        ) from None


def disable_warp(store: ConfigStore, opened: Config) -> Config:
    latest = store.load() or Config()
    if latest.warp != opened.warp:
        raise ConfigError("Integration settings changed externally. Cancel and reopen Settings.")
    disabled = replace(latest, warp=replace(latest.warp, enabled=False))
    try:
        store.save(disabled)
    except ConfigError:
        if store.load() != disabled:
            raise
    return disabled


def disconnect_warp(
    store: ConfigStore,
    opened: Config,
    *,
    keychain: Keychain | None = None,
) -> tuple[Config, bool]:
    disabled = disable_warp(store, opened)
    try:
        backend = keychain or Keychain()
        for identifier in (disabled.warp.credential_id, *disabled.warp.retired_credential_ids):
            backend.delete("warp", identifier)
        clean = replace(
            disabled,
            warp=replace(
                disabled.warp,
                credential_id="default",
                retired_credential_ids=(),
            ),
        )
        store.save(clean)
        return clean, True
    except (KeychainUnavailable, ConfigError):
        return disabled, False
