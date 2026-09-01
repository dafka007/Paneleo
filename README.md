# Paneleo

Paneleo is a Windows desktop comic reader that combines a local comic library with an embedded BatCave browsing and reading experience. The current version is the first public beta: `0.1.0-beta.1`.

## Main features

- Open individual comics or scan a folder into the local library.
- Resume standalone local comics from the home screen with saved page progress.
- Read CBZ, CBR, and PDF files.
- Browse BatCave in an embedded browser and track recent issues, reading-list entries, bookmarks, and reading progress.
- Navigate with on-screen controls, page clicks, or the keyboard.
- Use Fit Page, Fit Width, Actual Size, and adjustable zoom.
- Enter a distraction-free fullscreen reader while retaining compact reader controls.
- Restore the previous window size, position, or maximized state after fullscreen.

## Local comic reader

Choose **Open comic** for a single file or **Choose folder** to build a searchable local library. Paneleo records local reading progress and generates bounded cover thumbnails without modifying the original comic files.

Supported formats:

- CBZ (ZIP-based comic archives)
- CBR (RAR-based comic archives; requires 7-Zip)
- PDF

## BatCave integration

Paneleo includes an embedded BatCave browser and reader. It can retain reading-list entries, recent issues, bookmarks, issue progress, and bounded cover thumbnails. Live website behavior depends on BatCave and is intentionally excluded from the offline regression suite.

## Reader controls

Use the previous/next buttons, click the left or right side of a page, or use the arrow keys to turn pages. The reader also supports page selection, manga direction, Fit Page, Fit Width, Actual Size, and zoom from 50% to 250%.

Press `F11` to enter or leave fullscreen and `Esc` to leave it. In local-reader fullscreen, the Windows title bar, Paneleo sidebar, and standard reader chrome are hidden. A compact control strip provides page navigation, zoom, fit-page, back, and fullscreen-exit controls; it collapses automatically to keep the comic unobstructed. Fit Page recalculates for the fullscreen viewport.

## Requirements

- Windows x64
- 64-bit Python 3.10 through 3.14 (Python 3.12 recommended)
- 7-Zip for CBR/RAR archive extraction

Install 7-Zip from [7-zip.org](https://www.7-zip.org/) if you need CBR support. CBZ and PDF support do not require it.

## Windows distributions

The Windows installer installs Paneleo for all users under `C:\Program Files\Paneleo`, creates a Start Menu shortcut, and can optionally create a desktop shortcut. Installation, updates, and uninstalling require administrator permission. Python is not installed or required. Paneleo data remains separate for each Windows user in `%APPDATA%\Paneleo`, and uninstalling the application leaves that user data in place.

The portable ZIP is the no-install, no-admin option. Extract it, open the self-contained `Paneleo` folder, and run `Paneleo.exe`; Python and development tools are not required. Portable builds use the same `%APPDATA%\Paneleo` data location as installed builds.

7-Zip is not bundled with either distribution. Paneleo runs normally without it, but opening CBR/RAR-based comics requires a separate 7-Zip installation.

Current Windows binaries are unsigned, so Windows SmartScreen may show an **Unknown Publisher** warning.

## Run from source

1. Install a supported 64-bit Python from [python.org](https://www.python.org/downloads/windows/) and enable **Add python.exe to PATH** during installation.
2. Run `INSTALL.bat` once to create `.venv` and install the pinned runtime dependencies.
3. Run `RUN.bat` to start Paneleo.

`RUN.bat` validates that the virtual environment and core dependencies actually execute. If `.venv` is missing or invalid, it safely recreates the generated environment through `INSTALL.bat`.

## Development

The application uses Python, PySide6/Qt WebEngine, and PyMuPDF. Runtime dependencies are pinned in `requirements-win64.txt`; test dependencies are listed in `requirements-dev.txt`.

After running `INSTALL.bat`, install the test dependency with:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the complete offline regression suite with:

```bat
TEST.bat
```

The suite uses temporary data and does not read or modify normal Paneleo user data.

## Build the executable

Run:

```bat
BUILD_EXE.bat
```

The script installs the pinned PyInstaller version, creates a clean Windows build, and writes the packaged application to `dist\Paneleo\Paneleo.exe`. Keep the complete `dist\Paneleo` folder together.

To create both Windows release formats, install [Inno Setup 7](https://jrsoftware.org/isdl.php) and run:

```bat
BUILD_RELEASE.bat
```

The installer and portable ZIP are written to `release\`. Generated release artifacts are intentionally excluded from source control.

## Beta status

Paneleo `0.1.0-beta.1` is the first public beta. Back up important reading-list and bookmark data, and report reproducible issues with the file format and steps that triggered them.

## License

Paneleo is licensed under the [GNU General Public License v3.0](LICENSE).

Copyright (C) 2026 dafka007
