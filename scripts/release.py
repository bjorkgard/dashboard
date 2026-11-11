"""Validate release inputs and distributions without contacting GitHub."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path

_NUMBER = r"(?:0|[1-9][0-9]*)"
_VERSION = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?P<pre>(?:a|b|rc){_NUMBER})?\Z")


class ReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    prerelease: bool
    notes: str


def validate_release(root: Path, tag: str | None = None) -> ReleaseInfo:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        value = project["version"]
        if project["name"] != "dayboard" or not isinstance(value, str):
            raise ReleaseError("Expected the dayboard project and an explicit version.")
        match = _VERSION.fullmatch(value)
        if match is None:
            raise ReleaseError("Use X.Y.Z or a canonical X.Y.ZaN, X.Y.ZbN, or X.Y.ZrcN version.")
        if tag is not None and tag != f"v{value}":
            raise ReleaseError("Release tag must be v followed by the exact package version.")
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        entries = [
            package
            for package in lock.get("package", [])
            if package.get("name") == "dayboard" and package.get("source") == {"editable": "."}
        ]
        if len(entries) != 1 or entries[0].get("version") != value:
            raise ReleaseError("The uv lockfile project version is stale; run uv lock.")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        sections = list(re.finditer(r"^## \[([^\]\r\n]+)\][^\r\n]*$", changelog, re.MULTILINE))
        current = [index for index, section in enumerate(sections) if section.group(1) == value]
        if len(current) != 1:
            raise ReleaseError("Add exactly one matching version section to the changelog.")
        index = current[0]
        end = sections[index + 1].start() if index + 1 < len(sections) else len(changelog)
        notes = changelog[sections[index].end() : end].strip()
        if not notes or not re.search(r"^\s*[-*] \S", notes, re.MULTILINE):
            raise ReleaseError("The matching changelog section needs a nonempty change list.")
        return ReleaseInfo(value, match.group("pre") is not None, notes)
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        raise ReleaseError("Cannot read release metadata, uv lockfile, or changelog.") from None


def _check_metadata(raw: bytes, expected: str) -> None:
    metadata = BytesParser().parsebytes(raw)
    if (
        metadata.get("Name") != "dayboard"
        or metadata.get("Version") != expected
        or not str(metadata.get_payload()).strip()
    ):
        raise ReleaseError(
            "Distribution metadata or README description does not match the release."
        )


def inspect_distributions(dist: Path, info: ReleaseInfo) -> tuple[Path, Path]:
    wheel = dist / f"dayboard-{info.version}-py3-none-any.whl"
    source = dist / f"dayboard-{info.version}.tar.gz"
    actual = set(dist.glob("*.whl")) | set(dist.glob("*.tar.gz"))
    if actual != {wheel, source}:
        raise ReleaseError(
            "Expected only the current wheel and source distribution; use a clean dir."
        )
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_path = f"dayboard-{info.version}.dist-info/METADATA"
            _check_metadata(archive.read(metadata_path), info.version)
            if "dayboard/ui/styles/shell.tcss" not in archive.namelist():
                raise ReleaseError("Wheel is missing the terminal stylesheet.")
        with tarfile.open(source, "r:gz") as archive:
            prefix = f"dayboard-{info.version}/"
            required = {
                prefix + "README.md",
                prefix + "src/dayboard/ui/styles/shell.tcss",
                prefix + "PKG-INFO",
            }
            if not required.issubset(archive.getnames()):
                raise ReleaseError(
                    "Source distribution is missing README, stylesheet, or metadata."
                )
            stream = archive.extractfile(prefix + "PKG-INFO")
            if stream is None:
                raise ReleaseError("Source metadata is not a regular file.")
            with stream:
                _check_metadata(stream.read(), info.version)
    except (OSError, KeyError, zipfile.BadZipFile, tarfile.TarError):
        raise ReleaseError("Cannot inspect the built distributions.") from None
    return wheel, source


def prepare_release(root: Path, dist: Path, tag: str | None = None) -> ReleaseInfo:
    info = validate_release(root, tag)
    wheel, source = inspect_distributions(dist, info)
    sums = []
    for artifact in sorted((wheel, source)):
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        sums.append(f"{digest}  {artifact.name}\n")
    notes = (
        f"## Dayboard {info.version}\n\n{info.notes}\n\n"
        "## Install on a Mac\n\n"
        "Requires macOS, Python 3.13 or newer, and uv. Download the wheel and "
        "SHA256SUMS from this release. In the download directory, verify the files "
        "with `shasum -a 256 -c SHA256SUMS` "
        "(download both packages to check the whole manifest).\n\n"
        f"Install with `uv tool install --python 3.13 ./{wheel.name}`, then run "
        "`dayboard --demo` or `dayboard`. Use `--reinstall` when replacing an existing "
        "tool installation. Source installation is available through the attached tarball.\n\n"
        "These are Python packages, not a signed or notarized standalone macOS app. "
        "Checksums detect download corruption; they are not a code-signing guarantee. "
        "See the packaged README for implemented features, current limitations, and security.\n"
    )
    (dist / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")
    (dist / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "prepare"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag", help="exact release tag, e.g. v0.1.0")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            info = prepare_release(args.root, args.dist, args.tag)
        else:
            info = validate_release(args.root, args.tag)
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(f"version={info.version}\nprerelease={str(info.prerelease).lower()}\n")
        print(json.dumps({"version": info.version, "prerelease": info.prerelease}))
    except (ReleaseError, OSError) as error:
        message = str(error) if isinstance(error, ReleaseError) else "Cannot write release outputs."
        print(f"Release validation failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
