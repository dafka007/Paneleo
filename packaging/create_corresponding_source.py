"""Create the release's complete corresponding-source archive."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


VERSION = "0.1.0-beta.1"


@dataclass(frozen=True)
class SourceArchive:
    filename: str
    url: str
    size: int
    sha256: str
    component: str


SOURCES = (
    SourceArchive(
        "pymupdf-1.28.2.tar.gz",
        "https://files.pythonhosted.org/packages/a3/fb/b6761fa2d5266f2cdb24c3b91f4023070ab7848381417678e7a289a1d52a/pymupdf-1.28.2.tar.gz",
        87_903_557,
        "5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249",
        "PyMuPDF 1.28.2",
    ),
    SourceArchive(
        "mupdf-1.28.2-source.tar.gz",
        "https://mupdf.com/downloads/archive/mupdf-1.28.2-source.tar.gz",
        68_898_646,
        "44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156",
        "MuPDF 1.28.2 (including third-party source)",
    ),
    SourceArchive(
        "pyside-setup-everywhere-src-6.11.2.tar.xz",
        "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz",
        18_053_248,
        "cba47efbaad1bedd529725cbc14e21f156c7a19366f07b3edfbb076ffd7afdf8",
        "PySide6 and Shiboken 6.11.2",
    ),
    SourceArchive(
        "qt-everywhere-src-6.11.2.tar.xz",
        "https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz",
        1_019_661_552,
        "6dcfbca271d76a6502741a2c0dc6fc98ef7dd0b7b4cfd0abcebb285a86a26f33",
        "Qt 6.11.2, including Qt WebEngine and Chromium",
    ),
    SourceArchive(
        "pyinstaller-6.22.2.tar.gz",
        "https://files.pythonhosted.org/packages/cc/2b/836d9def811c02522e0921d8b8cdf0c16b0545a216e97e71041758057859/pyinstaller-6.22.2.tar.gz",
        4_092_631,
        "89b65a3ad07d9dd5832253e37bc45f31872d10d7f9d5c9fd0fdd6088a83829dd",
        "PyInstaller 6.22.2",
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def obtain(source: SourceArchive, cache: Path) -> Path:
    destination = cache / source.filename
    if destination.is_file():
        if destination.stat().st_size == source.size and sha256_file(destination) == source.sha256:
            print(f"Using verified cached source: {source.filename}", flush=True)
            return destination
        destination.unlink()

    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    print(f"Downloading {source.component}: {source.url}", flush=True)
    with urllib.request.urlopen(source.url) as response, partial.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if partial.stat().st_size != source.size:
        partial.unlink()
        raise RuntimeError(f"Unexpected size for {source.filename}")
    if sha256_file(partial) != source.sha256:
        partial.unlink()
        raise RuntimeError(f"SHA-256 mismatch for {source.filename}")
    os.replace(partial, destination)
    return destination


def paneleo_archive(project_root: Path, ref: str) -> bytes:
    result = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=project_root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def manifest(ref: str) -> str:
    lines = [
        f"Paneleo corresponding source for {VERSION}",
        f"Paneleo Git revision: {ref}",
        "",
        "Bundled upstream source archives:",
    ]
    for source in SOURCES:
        lines.extend(
            (
                f"- {source.component}",
                f"  File: {source.filename}",
                f"  Size: {source.size} bytes",
                f"  SHA-256: {source.sha256}",
                f"  Upstream: {source.url}",
            )
        )
    lines.extend(
        (
            "",
            "The Qt source archive is the complete upstream distribution and includes",
            "Qt WebEngine, Chromium, and the third-party source shipped with Qt 6.11.2.",
            "All upstream archives are stored byte-for-byte with their verified hashes.",
            "",
        )
    )
    return "\n".join(lines)


def create(project_root: Path, output: Path, cache: Path, ref: str) -> None:
    project_root = project_root.resolve()
    output = output.resolve()
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    archives = [(source, obtain(source, cache)) for source in SOURCES]

    revision = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tar_data = paneleo_archive(project_root, ref)
    root = f"Paneleo-Corresponding-Source-{VERSION}"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with zipfile.ZipFile(output, "w", allowZip64=True) as target:
        with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as paneleo:
            for member in paneleo.getmembers():
                if not member.isfile():
                    continue
                extracted = paneleo.extractfile(member)
                if extracted is None:
                    continue
                target.writestr(
                    f"{root}/Paneleo-{VERSION}/{member.name}",
                    extracted.read(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        target.writestr(
            f"{root}/SOURCE_MANIFEST.txt",
            manifest(revision),
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )
        for source, path in archives:
            target.write(
                path,
                f"{root}/third-party-source/{source.filename}",
                compress_type=zipfile.ZIP_STORED,
            )
    print(f"Created {output} ({output.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path(".tmp/source-cache"))
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    create(args.project_root, args.output, args.cache, args.ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

