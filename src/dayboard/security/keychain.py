"""Use only the explicit macOS Keychain backend, never keyring autodiscovery."""

import sys
from typing import Protocol, cast


class KeychainUnavailable(RuntimeError):
    pass


class Backend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


def native_backend() -> Backend:
    if sys.platform != "darwin":
        raise KeychainUnavailable("macOS Keychain is required; no plaintext fallback is allowed.")
    from keyring.backends.macOS import Keyring

    backend = Keyring()  # type: ignore[no-untyped-call]  # Third-party constructor lacks annotations.
    if backend.priority <= 0:
        raise KeychainUnavailable("macOS Keychain is unavailable.")
    # Avoid environment-configured alternate keychains and their warning paths.
    backend.keychain = None
    return cast(Backend, backend)


class Keychain:
    def __init__(self, *, backend: Backend | None = None) -> None:
        if backend is not None:
            self._backend = backend
            return
        try:
            self._backend = native_backend()
        except Exception:
            raise KeychainUnavailable(
                "macOS Keychain is unavailable; no plaintext fallback is allowed."
            ) from None

    @staticmethod
    def _service(provider: str, account: str) -> str:
        from dayboard.security.identifiers import validate_identifier

        validate_identifier(provider)
        validate_identifier(account)
        return f"dev.dayboard.credentials.{provider}"

    def get(self, provider: str, account: str) -> str | None:
        service = self._service(provider, account)
        try:
            return self._backend.get_password(service, account)
        except Exception:
            raise KeychainUnavailable(
                "Keychain read failed; unlock or authorize Keychain."
            ) from None

    def set(self, provider: str, account: str, secret: str) -> None:
        service = self._service(provider, account)
        if not isinstance(secret, str) or not secret:
            raise ValueError("A nonempty credential is required.")
        try:
            self._backend.set_password(service, account, secret)
        except Exception:
            raise KeychainUnavailable("Keychain write failed; no credential was cached.") from None

    def delete(self, provider: str, account: str) -> None:
        service = self._service(provider, account)
        try:
            # A failed read is not treated as an absent credential.
            if self._backend.get_password(service, account) is not None:
                self._backend.delete_password(service, account)
        except Exception:
            raise KeychainUnavailable(
                "Keychain deletion failed; authorization is required."
            ) from None
