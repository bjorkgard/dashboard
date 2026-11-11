import pytest

from dayboard.security.keychain import Keychain, KeychainUnavailable


class FakeNativeBackend:
    """Synthetic adapter, never reads or writes the system Keychain."""

    def __init__(self):
        self.items = {}
        self.locked = False

    def get_password(self, service, username):
        if self.locked:
            raise RuntimeError("locked: SYNTHETIC_SECRET")
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        if self.locked:
            raise RuntimeError("locked: SYNTHETIC_SECRET")
        self.items[service, username] = password

    def delete_password(self, service, username):
        if self.locked:
            raise RuntimeError("locked: SYNTHETIC_SECRET")
        self.items.pop((service, username), None)


@pytest.fixture
def vault():
    backend = FakeNativeBackend()
    return Keychain(backend=backend), backend


def test_credentials_are_isolated_by_provider_and_account(vault):
    keychain, _ = vault
    keychain.set("google", "work", "fake-work")
    keychain.set("google", "personal", "fake-personal")
    keychain.set("microsoft", "work", "fake-ms")
    assert keychain.get("google", "work") == "fake-work"
    assert keychain.get("google", "personal") == "fake-personal"
    assert keychain.get("microsoft", "work") == "fake-ms"


def test_delete_is_idempotent_and_only_removes_selected_account(vault):
    keychain, _ = vault
    keychain.set("google", "one", "fake-one")
    keychain.set("google", "two", "fake-two")
    keychain.delete("google", "one")
    keychain.delete("google", "one")
    assert keychain.get("google", "one") is None
    assert keychain.get("google", "two") == "fake-two"


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_locked_keychain_fails_closed_with_no_exception_leak(vault, operation):
    keychain, backend = vault
    backend.locked = True
    args = ("google", "work", "fake") if operation == "set" else ("google", "work")
    with pytest.raises(KeychainUnavailable) as error:
        getattr(keychain, operation)(*args)
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert error.value.__suppress_context__
    assert not backend.items


def test_default_backend_never_uses_auto_selected_keyring(monkeypatch):
    import dayboard.security.keychain as module

    monkeypatch.setattr(module.sys, "platform", "linux")
    with pytest.raises(KeychainUnavailable, match="macOS"):
        Keychain()


def test_native_backend_constructor_failure_is_redacted(monkeypatch):
    import dayboard.security.keychain as module

    def broken():
        raise RuntimeError("SYNTHETIC_SECRET")

    monkeypatch.setattr(module, "native_backend", broken)
    with pytest.raises(KeychainUnavailable) as error:
        Keychain()
    assert "SYNTHETIC_SECRET" not in str(error.value)


def test_account_identifiers_cannot_escape_service_namespace(vault):
    keychain, _ = vault
    with pytest.raises(ValueError):
        keychain.set("google/work", "account", "fake")
