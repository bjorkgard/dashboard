import hashlib
import io
import tarfile
import zipfile

import pytest

from scripts.release import ReleaseError, main, prepare_release, validate_release


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "dayboard"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "dayboard"\nversion = "1.2.3"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\nFuture work.\n\n"
        "## [1.2.3]\n\n### Fixed\n\n- Synthetic test improvement.\n\n"
        "## [1.2.2]\n\nOlder notes.\n",
        encoding="utf-8",
    )
    return tmp_path


def set_version(project, value):
    for name in ("pyproject.toml", "uv.lock"):
        path = project / name
        path.write_text(path.read_text().replace("1.2.3", value), encoding="utf-8")
    path = project / "CHANGELOG.md"
    path.write_text(path.read_text().replace("[1.2.3]", f"[{value}]"), encoding="utf-8")


def make_distributions(project, *, wheel_version="1.2.3", source_version="1.2.3", css=True):
    dist = project / "dist"
    dist.mkdir(exist_ok=True)
    metadata = "Metadata-Version: 2.4\nName: dayboard\nVersion: {}\n\n# Dayboard\n"
    with zipfile.ZipFile(dist / "dayboard-1.2.3-py3-none-any.whl", "w") as wheel:
        wheel.writestr("dayboard-1.2.3.dist-info/METADATA", metadata.format(wheel_version))
        if css:
            wheel.writestr("dayboard/ui/styles/shell.tcss", "Screen { color: white; }")
    with tarfile.open(dist / "dayboard-1.2.3.tar.gz", "w:gz") as source:
        for name, content in {
            "PKG-INFO": metadata.format(source_version),
            "README.md": "# Dayboard\n",
            "src/dayboard/ui/styles/shell.tcss": "Screen { color: white; }",
        }.items():
            data = content.encode()
            info = tarfile.TarInfo("dayboard-1.2.3/" + name)
            info.size = len(data)
            source.addfile(info, io.BytesIO(data))
    return dist


def test_stable_release_matches_tag_lock_and_only_current_notes(project):
    info = validate_release(project, "v1.2.3")
    assert info.version == "1.2.3"
    assert not info.prerelease
    assert "Synthetic test improvement." in info.notes
    assert "Older notes." not in info.notes
    assert "Future work." not in info.notes


@pytest.mark.parametrize("version", ["1.3.0a0", "1.3.0b2", "1.3.0rc1"])
def test_prereleases_are_automatically_classified(project, version):
    set_version(project, version)
    info = validate_release(project, f"v{version}")
    assert info.prerelease
    assert info.version == version


@pytest.mark.parametrize(
    "value",
    [
        "v1.2.4",
        "1.2.3",
        "v01.2.3",
        "v1.2.3;whoami",
        "v1.2.3\nversion=9.9.9",
    ],
)
def test_invalid_or_mismatched_tags_cannot_publish(project, value):
    with pytest.raises(ReleaseError):
        validate_release(project, value)


@pytest.mark.parametrize("value", ["01.2.3", "1.2", "1.2.3.dev1", "1.2.3+local", "1.2.3rc01"])
def test_noncanonical_package_versions_are_rejected(project, value):
    set_version(project, value)
    with pytest.raises(ReleaseError):
        validate_release(project)


def test_stale_lockfile_is_rejected(project):
    lock = project / "uv.lock"
    lock.write_text(lock.read_text().replace('version = "1.2.3"', 'version = "1.2.2"'))
    with pytest.raises(ReleaseError, match="lock"):
        validate_release(project)


def test_missing_project_lock_entry_is_rejected(project):
    (project / "uv.lock").write_text("version = 1\n")
    with pytest.raises(ReleaseError, match="lock"):
        validate_release(project)


def test_missing_or_empty_release_notes_are_rejected(project):
    (project / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    with pytest.raises(ReleaseError, match="changelog"):
        validate_release(project, "v1.2.3")
    (project / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3]\n\n## [1.2.2]\nOld")
    with pytest.raises(ReleaseError, match="changelog"):
        validate_release(project, "v1.2.3")


def test_preparation_verifies_packages_and_writes_checksums_and_notes(project):
    dist = make_distributions(project)
    info = prepare_release(project, dist, "v1.2.3")
    assert info.version == "1.2.3"
    lines = (dist / "SHA256SUMS").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        checksum, name = line.split("  ")
        assert checksum == hashlib.sha256((dist / name).read_bytes()).hexdigest()
    notes = (dist / "RELEASE_NOTES.md").read_text()
    assert "Synthetic test improvement." in notes
    assert "Python 3.13" in notes
    assert "dayboard-1.2.3-py3-none-any.whl" in notes
    assert "not a signed" in notes


@pytest.mark.parametrize(
    "arguments",
    [
        {"wheel_version": "1.2.2"},
        {"source_version": "1.2.2"},
        {"css": False},
    ],
)
def test_wrong_metadata_or_missing_stylesheet_blocks_preparation(project, arguments):
    dist = make_distributions(project, **arguments)
    with pytest.raises(ReleaseError):
        prepare_release(project, dist, "v1.2.3")
    assert not (dist / "SHA256SUMS").exists()


def test_cli_writes_only_validated_github_outputs(project, tmp_path, capsys):
    output = tmp_path / "outputs"
    assert (
        main(
            [
                "validate",
                "--root",
                str(project),
                "--tag",
                "v1.2.3",
                "--github-output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_text() == "version=1.2.3\nprerelease=false\n"
    assert '"version": "1.2.3"' in capsys.readouterr().out


def test_invalid_cli_input_does_not_emit_outputs(project, tmp_path, capsys):
    output = tmp_path / "outputs"
    assert (
        main(
            [
                "validate",
                "--root",
                str(project),
                "--tag",
                "v1.2.4",
                "--github-output",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()
    assert "Release validation failed" in capsys.readouterr().err


def test_stale_distributions_are_not_accidentally_published(project):
    dist = make_distributions(project)
    (dist / "dayboard-0.0.1-py3-none-any.whl").write_bytes(b"old")
    with pytest.raises(ReleaseError, match="distribution"):
        prepare_release(project, dist, "v1.2.3")


def test_missing_distribution_is_rejected(project):
    dist = project / "dist"
    dist.mkdir()
    with pytest.raises(ReleaseError):
        prepare_release(project, dist, "v1.2.3")
