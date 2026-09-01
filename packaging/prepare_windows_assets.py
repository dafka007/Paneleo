"""Generate Windows icon and executable version metadata from Paneleo source."""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import struct
import sys
import tempfile


def read_app_version(app_path: Path) -> str:
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise RuntimeError("APP_VERSION was not found in app.py")


def numeric_version(version: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z]+[.-]?(\d+))?", version)
    if not match:
        raise RuntimeError(f"Unsupported Paneleo version format: {version}")
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), int(prerelease or 0)


def write_version_info(path: Path, version: str, fixed_version: tuple[int, int, int, int]) -> None:
    values = ", ".join(str(value) for value in fixed_version)
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({values}),
    prodvers=({values}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('FileDescription', 'Paneleo Comic Reader'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'Paneleo'),
         StringStruct('OriginalFilename', 'Paneleo.exe'),
         StringStruct('ProductName', 'Paneleo'),
         StringStruct('ProductVersion', '{version}')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def paneleo_icon_pngs(app_path: Path) -> list[tuple[int, bytes]]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    original_appdata = os.environ.get("APPDATA")
    with tempfile.TemporaryDirectory(prefix="paneleo_packaging_") as temp_appdata:
        os.environ["APPDATA"] = temp_appdata
        sys.path.insert(0, str(app_path.parent))
        try:
            import app as paneleo
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice
            from PySide6.QtWidgets import QApplication

            application = QApplication.instance() or QApplication([])
            images = []
            for size in (16, 24, 32, 48, 64, 128, 256):
                pixmap = paneleo.build_paneleo_icon(size).pixmap(size, size)
                data = QByteArray()
                buffer = QBuffer(data)
                if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not pixmap.save(buffer, "PNG"):
                    raise RuntimeError(f"Could not render the {size}px Paneleo icon")
                buffer.close()
                images.append((size, bytes(data)))
            application.processEvents()
            return images
        finally:
            sys.path.pop(0)
            if original_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = original_appdata


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    header_size = 6 + (16 * len(images))
    offset = header_size
    entries = []
    payload = []
    for size, png in images:
        dimension = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(png), offset))
        payload.append(png)
        offset += len(png)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(payload))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--print-numeric-version", action="store_true")
    args = parser.parse_args()

    app_path = args.app.resolve()
    version = read_app_version(app_path)
    fixed_version = numeric_version(version)
    if args.print_version:
        print(version)
        return 0
    if args.print_numeric_version:
        print(".".join(str(value) for value in fixed_version))
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required unless a print option is used")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    icon_path = output_dir / "Paneleo.ico"
    version_path = output_dir / "Paneleo-version.txt"
    write_ico(icon_path, paneleo_icon_pngs(app_path))
    write_version_info(version_path, version, fixed_version)
    print(f"Generated {icon_path}")
    print(f"Generated {version_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
