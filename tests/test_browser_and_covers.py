import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QUrl
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QProgressBar


class CardProgressWindowWatcher(QObject):
    def __init__(self):
        super().__init__()
        self.top_level_shows = 0

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, QProgressBar)
            and obj.objectName() == "CardProgress"
            and obj.isWindow()
        ):
            self.top_level_shows += 1
        return False


def test_batcave_webengine_initializes_offline_without_exceptions(
    main_window, paneleo_module, network_blocker
):
    assert main_window.browser.web.page() is not None
    assert paneleo_module.is_allowed_batcave_url(main_window.browser.web.url())
    before = len(network_blocker.blocked_urls)
    main_window.browser.web.reload()
    QTest.qWait(350)
    assert len(network_blocker.blocked_urls) > before
    assert any(url.startswith("https://batcave.biz/") for url in network_blocker.blocked_urls)


def test_batcave_url_allowlist_rejects_unsafe_urls(paneleo_module):
    assert paneleo_module.is_allowed_batcave_url(QUrl("https://batcave.biz/reader/123"))
    assert paneleo_module.is_allowed_batcave_url(QUrl("https://www.batcave.biz/"))
    assert not paneleo_module.is_allowed_batcave_url(QUrl("http://batcave.biz/"))
    assert not paneleo_module.is_allowed_batcave_url(QUrl("https://example.com/"))
    assert paneleo_module.is_allowed_batcave_asset_url(QUrl("https://img.batcave.biz/cover.jpg"))
    assert not paneleo_module.is_allowed_batcave_asset_url(QUrl("https://batcave.biz.evil.test/x"))


def test_recent_progress_never_becomes_a_top_level_window(main_window, qt_app):
    main_window.batcave_library["issues"] = {
        "https://batcave.biz/reader/regression": {
            "url": "https://batcave.biz/reader/regression",
            "title": "Read Regression Series #1 comics online for free",
            "series": "Regression Series",
            "issue": "1",
            "current_page": 3,
            "total_pages": 20,
            "last_opened": int(time.time()),
        }
    }
    watcher = CardProgressWindowWatcher()
    qt_app.installEventFilter(watcher)
    try:
        main_window.refresh_recent_list()
        QTest.qWait(80)
    finally:
        qt_app.removeEventFilter(watcher)

    progress_bars = main_window.home_recent_list.findChildren(
        QProgressBar, "CardProgress"
    )
    assert progress_bars
    assert all(progress.parentWidget() is not None for progress in progress_bars)
    assert watcher.top_level_shows == 0


def test_local_cover_generation_uses_isolated_cache(main_window, paneleo_module, comic_files):
    first = main_window.get_cover(comic_files.cbz)
    assert first is not None
    path = Path(first)
    assert path.is_file()
    assert path.is_relative_to(paneleo_module.CACHE_DIR)
    second = main_window.get_cover(comic_files.cbz)
    assert second == first
    image = paneleo_module.safe_load_qimage(path)
    assert not image.isNull()


def test_batcave_thumbnail_cache_round_trip(main_window, paneleo_module):
    image = QImage(240, 360, QImage.Format.Format_RGB32)
    image.fill(QColor("#784fa8"))
    pixmap = QPixmap.fromImage(image)
    main_window._save_cached_cover("Regression Series", pixmap)
    cache_path = main_window._cover_cache_path("Regression Series")
    assert cache_path is not None and cache_path.is_file()
    assert cache_path.parent == paneleo_module.BATCAVE_COVER_CACHE_DIR
    assert cache_path.suffix == ".jpg"
    loaded = main_window._load_cached_cover("Regression Series")
    assert not loaded.isNull()
