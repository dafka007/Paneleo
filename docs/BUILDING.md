# Building Paneleo

These instructions are for working from source on 64-bit Windows. Python 3.12 is recommended; Python 3.10 through 3.14 is supported by the current dependency lockfile.

## Set up the environment

From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --only-binary=:all: --require-hashes -r requirements-win64.txt
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run Paneleo from source:

```powershell
.venv\Scripts\python.exe app.py
```

CBR/RAR support also requires [7-Zip](https://www.7-zip.org/) to be installed.

## Run the tests

The normal regression suite is offline and uses temporary test data:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Live BatCave website behavior is not part of the automated suite.

## Build the Windows application

Install the pinned PyInstaller version:

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller==6.22.2
```

Create a clean folder build:

```powershell
Remove-Item build, dist, packaging\generated -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Paneleo.spec -Force -ErrorAction SilentlyContinue
.venv\Scripts\python.exe packaging\prepare_windows_assets.py --app app.py --output-dir packaging\generated
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name Paneleo --icon packaging\generated\Paneleo.ico --version-file packaging\generated\Paneleo-version.txt --collect-all PySide6.QtWebEngineCore --collect-all PySide6.QtWebEngineWidgets app.py
.venv\Scripts\python.exe packaging\finalize_distribution.py --project-root . --distribution dist\Paneleo
```

The result is `dist\Paneleo\Paneleo.exe`. Keep the complete `Paneleo` folder together.

## Build release files

Install [Inno Setup 7](https://jrsoftware.org/isdl.php), make sure `ISCC.exe` is available, and create the release directory:

```powershell
New-Item -ItemType Directory -Force release | Out-Null
.venv\Scripts\python.exe packaging\create_portable_zip.py --source dist\Paneleo --output release\Paneleo-Portable-0.1.0-beta.1.zip
ISCC.exe /Qp "/DAppVersion=0.1.0-beta.1" "/DAppVersionNumeric=0.1.0.1" "/DSourceDir=$PWD\dist\Paneleo" "/DOutputDir=$PWD\release" packaging\Paneleo.iss
.venv\Scripts\python.exe packaging\create_corresponding_source.py --project-root . --ref HEAD --output release\Paneleo-Corresponding-Source-0.1.0-beta.1.zip
```

The corresponding-source step downloads the exact upstream source archives listed in the script, verifies their hashes, and caches them under `.tmp\source-cache`.

Before distributing a build, run the tests again and check the installer, portable ZIP, license files, source archive, and Git status.
