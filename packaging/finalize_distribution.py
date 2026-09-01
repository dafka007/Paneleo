"""Finalize and validate the redistributable PyInstaller folder."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


NOTICE_FILES = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "CORRESPONDING_SOURCE.md",
)

# These files are supplied by the centrally serviced Microsoft Visual C++
# Redistributable. Paneleo does not claim rights to redistribute local copies.
MICROSOFT_RUNTIME_DLLS = {
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
}


def finalize(project_root: Path, distribution: Path) -> list[Path]:
    project_root = project_root.resolve()
    distribution = distribution.resolve()
    executable = distribution / "Paneleo.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Packaged executable is missing: {executable}")

    removed: list[Path] = []
    for path in distribution.rglob("*.dll"):
        if path.name.lower() in MICROSOFT_RUNTIME_DLLS:
            path.unlink()
            removed.append(path)

    for name in NOTICE_FILES:
        source = project_root / name
        if not source.is_file():
            raise FileNotFoundError(f"Required notice is missing: {source}")
        shutil.copy2(source, distribution / name)

    source_licenses = project_root / "licenses"
    if not source_licenses.is_dir():
        raise FileNotFoundError(f"License directory is missing: {source_licenses}")
    target_licenses = distribution / "licenses"
    if target_licenses.exists():
        shutil.rmtree(target_licenses)
    shutil.copytree(source_licenses, target_licenses)

    remaining = [
        path
        for path in distribution.rglob("*.dll")
        if path.name.lower() in MICROSOFT_RUNTIME_DLLS
    ]
    if remaining:
        raise RuntimeError(f"Microsoft runtime DLLs remain in payload: {remaining}")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--distribution", type=Path, required=True)
    args = parser.parse_args()
    removed = finalize(args.project_root, args.distribution)
    print(f"Copied release notices and removed {len(removed)} Microsoft runtime DLL(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

