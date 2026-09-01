import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINALIZER_PATH = PROJECT_ROOT / "packaging" / "finalize_distribution.py"
SPEC = importlib.util.spec_from_file_location("paneleo_finalize_distribution", FINALIZER_PATH)
assert SPEC and SPEC.loader
FINALIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINALIZER)
MICROSOFT_RUNTIME_DLLS = FINALIZER.MICROSOFT_RUNTIME_DLLS


def test_repository_uses_agpl_and_has_release_notices():
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 19 November 2007" in license_text
    for name in ("THIRD_PARTY_NOTICES.md", "CORRESPONDING_SOURCE.md"):
        assert (PROJECT_ROOT / name).is_file()


def test_packaging_copies_notices_and_omits_microsoft_runtime_dlls(tmp_path):
    distribution = tmp_path / "Paneleo"
    distribution.mkdir()
    (distribution / "Paneleo.exe").write_bytes(b"test executable")
    nested = distribution / "_internal" / "PySide6"
    nested.mkdir(parents=True)
    for name in MICROSOFT_RUNTIME_DLLS:
        (nested / name).write_bytes(b"runtime")

    removed = FINALIZER.finalize(PROJECT_ROOT, distribution)
    assert len(removed) == len(MICROSOFT_RUNTIME_DLLS)
    assert not any(path.name.lower() in MICROSOFT_RUNTIME_DLLS for path in distribution.rglob("*.dll"))
    assert (distribution / "LICENSE").is_file()
    assert (distribution / "THIRD_PARTY_NOTICES.md").is_file()
    assert (distribution / "CORRESPONDING_SOURCE.md").is_file()
    assert (distribution / "licenses" / "AGPL-3.0.txt").is_file()


def test_release_builder_creates_all_three_release_assets():
    script = (PROJECT_ROOT / "BUILD_RELEASE.bat").read_text(encoding="utf-8")
    assert "Paneleo-Setup-%APP_VERSION%.exe" in script
    assert "Paneleo-Portable-%APP_VERSION%.zip" in script
    assert "Paneleo-Corresponding-Source-%APP_VERSION%.zip" in script
