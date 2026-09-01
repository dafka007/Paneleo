"""Create a portable Paneleo ZIP from a tested PyInstaller folder build."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    executable = source / "Paneleo.exe"
    if not source.is_dir() or not executable.is_file():
        raise RuntimeError("The PyInstaller Paneleo folder build is missing or incomplete")

    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError("The PyInstaller Paneleo folder contains no files")
    if any(path.is_symlink() for path in files):
        raise RuntimeError("Portable releases cannot contain symbolic links")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in files:
            archive.write(path, (Path(source.name) / path.relative_to(source)).as_posix())

    print(f"Created {output} with {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
