import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dayboard.integrations.mail.bridge import compile_bridge

SOURCE = Path(__file__).parents[2] / "src/dayboard/native/MailBridge.swift"


def test_native_read_only_static_contract():
    text = SOURCE.read_text()
    assert 'eventID: code("getd")' in text
    assert "typeKernelProcessID" in text
    assert "AEDeterminePermissionToAutomateTarget" in text
    assert 'operation == "authorize"' in text
    for forbidden in (
        "ScriptingBridge",
        "NSAppleScript",
        "setd",
        'code("pALL")',
        'code("ctnt")',
        'code("rsrc")',
        'code("pswd")',
        "launchApplication",
        "openApplication",
    ):
        assert forbidden not in text
    assert re.findall(r'eventID: code\("([^"]+)"\)', text) == ["getd"]
    properties = text.split("private enum Property: String {", 1)[1].split("}", 1)[0]
    assert set(re.findall(r'= "([^"]+)"', properties)) == {
        "ID  ",
        "pnam",
        "sndr",
        "subj",
        "rdrc",
        "isrd",
    }
    assert ".neverInteract" in text
    assert "reader.truncated = true" in text
    assert "ProcessInfo.processInfo.systemUptime + 8" in text


@pytest.mark.skipif(sys.platform != "darwin", reason="Installed static dictionary is macOS-only")
def test_property_codes_match_installed_dictionary():
    root = ET.parse("/System/Applications/Mail.app/Contents/Resources/Mail.sdef")
    actual = {(p.get("name"), p.get("code")) for p in root.iter("property")}
    assert {
        ("id", "ID  "),
        ("sender", "sndr"),
        ("subject", "subj"),
        ("date received", "rdrc"),
        ("read status", "isrd"),
        ("name", "pnam"),
    } <= actual


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None,
    reason="Swift requires macOS Command Line Tools",
)
async def test_native_compiles_without_running_or_sending_events(tmp_path):
    target = tmp_path / "mail-bridge"
    await compile_bridge(target)
    assert target.is_file()
