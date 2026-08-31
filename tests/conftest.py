import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# app.py resolves all persistent files while it is imported. Set an isolated
# process-local AppData root before any test imports Paneleo, then remove it at
# session end. The user's real Paneleo settings/cache are never opened.
_ORIGINAL_APPDATA = os.environ.get("APPDATA")
TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="paneleo_pytest_"))
TEST_APPDATA = TEST_RUNTIME_ROOT / "AppData"
TEST_APPDATA.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(TEST_APPDATA)


class OfflineRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Block live network traffic while still initializing the real WebEngine."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.blocked_urls = []

    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme().lower() in {"http", "https"}:
            self.blocked_urls.append(url.toString())
            info.block(True)


def pytest_sessionfinish(session, exitstatus):
    if _ORIGINAL_APPDATA is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _ORIGINAL_APPDATA
    shutil.rmtree(TEST_RUNTIME_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def paneleo_module():
    module = importlib.import_module("app")
    assert Path(module.SETTINGS_FILE).is_relative_to(TEST_APPDATA)
    assert Path(module.PROGRESS_FILE).is_relative_to(TEST_APPDATA)
    assert Path(module.BATCAVE_LIBRARY_FILE).is_relative_to(TEST_APPDATA)
    return module


@pytest.fixture(scope="session")
def qt_app(paneleo_module):
    application = QApplication.instance() or QApplication(["paneleo-pytest"])
    QCoreApplication.setApplicationName("PaneleoTest")
    blocker = OfflineRequestInterceptor(application)
    QWebEngineProfile.defaultProfile().setUrlRequestInterceptor(blocker)
    application._paneleo_test_network_blocker = blocker
    yield application
    application.closeAllWindows()
    application.processEvents()


@pytest.fixture(scope="session")
def network_blocker(qt_app):
    return qt_app._paneleo_test_network_blocker


@pytest.fixture
def clean_user_data(paneleo_module):
    for path in (
        paneleo_module.SETTINGS_FILE,
        paneleo_module.PROGRESS_FILE,
        paneleo_module.BATCAVE_LIBRARY_FILE,
    ):
        Path(path).unlink(missing_ok=True)
    shutil.rmtree(paneleo_module.CACHE_DIR, ignore_errors=True)
    paneleo_module.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paneleo_module.BATCAVE_COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def main_window(qt_app, paneleo_module, clean_user_data):
    window = paneleo_module.MainWindow()
    window.show()
    QTest.qWait(180)
    yield window
    if window.isFullScreen():
        window.exit_window_fullscreen()
        QTest.qWait(80)
    window.close()
    qt_app.processEvents()
    QTest.qWait(40)


@pytest.fixture
def comic_files(tmp_path, qt_app, paneleo_module):
    pages = []
    for number, color in enumerate(("#c84f4f", "#417fc2", "#50a26b"), start=1):
        image = QImage(360, 540, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        path = tmp_path / f"page-{number:02}.png"
        assert image.save(str(path), "PNG")
        pages.append(path)

    cbz = tmp_path / "regression.cbz"
    with zipfile.ZipFile(cbz, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in pages:
            archive.write(path, path.name)

    pdf = tmp_path / "regression.pdf"
    document = paneleo_module.fitz.open()
    for number in range(1, 3):
        page = document.new_page(width=360, height=540)
        page.insert_text((36, 72), f"Paneleo regression page {number}")
    document.save(str(pdf))
    document.close()

    seven_zip = paneleo_module.find_7zip()
    cbr = None
    if seven_zip:
        cbr = tmp_path / "regression.cbr"
        creation = subprocess.run(
            [seven_zip, "a", "-t7z", str(cbr), *(path.name for path in pages)],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert creation.returncode == 0, creation.stdout
        assert cbr.is_file()

    return SimpleNamespace(pages=pages, cbz=cbz, pdf=pdf, cbr=cbr, seven_zip=seven_zip)
