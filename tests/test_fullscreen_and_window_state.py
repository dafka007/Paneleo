import json

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


def _assert_rect_close(actual, expected, tolerance=2):
    assert abs(actual.x() - expected.x()) <= tolerance
    assert abs(actual.y() - expected.y()) <= tolerance
    assert abs(actual.width() - expected.width()) <= tolerance
    assert abs(actual.height() - expected.height()) <= tolerance


def test_reader_fullscreen_hides_chrome_refits_and_restores_geometry(main_window, comic_files):
    main_window.open_comic(str(comic_files.cbz))
    main_window.showNormal()
    main_window.resize(1180, 760)
    main_window.move(120, 90)
    QTest.qWait(180)
    expected_geometry = main_window.geometry()
    before = main_window.reader.image_label.pixmap()
    assert before is not None and not before.isNull()

    main_window.enter_window_fullscreen()
    QTest.qWait(450)
    assert main_window.isFullScreen()
    assert not main_window.sidebar.isVisible()
    assert not main_window.mini_sidebar.isVisible()
    assert not main_window.reader.reader_bar.isVisible()
    assert not main_window.reader.reader_footer.isVisible()
    fullscreen_pixmap = main_window.reader.image_label.pixmap()
    assert fullscreen_pixmap is not None and not fullscreen_pixmap.isNull()
    assert fullscreen_pixmap.width() > 0 and fullscreen_pixmap.height() > 0

    main_window.exit_window_fullscreen()
    QTest.qWait(450)
    assert not main_window.isFullScreen()
    assert main_window.reader.reader_bar.isVisible()
    assert main_window.reader.reader_footer.isVisible()
    _assert_rect_close(main_window.geometry(), expected_geometry)


def test_f11_toggles_fullscreen_and_escape_exits(main_window, comic_files):
    main_window.open_comic(str(comic_files.cbz))
    QTest.keyClick(main_window, Qt.Key.Key_F11)
    QTest.qWait(400)
    assert main_window.isFullScreen()
    QTest.keyClick(main_window, Qt.Key.Key_Escape)
    QTest.qWait(400)
    assert not main_window.isFullScreen()


def test_maximized_state_restores_after_fullscreen(main_window, comic_files):
    main_window.open_comic(str(comic_files.cbz))
    main_window.showMaximized()
    QTest.qWait(350)
    assert main_window.isMaximized()
    main_window.enter_window_fullscreen()
    QTest.qWait(350)
    assert main_window.isFullScreen()
    main_window.exit_window_fullscreen()
    QTest.qWait(450)
    assert main_window.isMaximized()


def test_closing_fullscreen_preserves_previous_windowed_state(
    qt_app, paneleo_module, clean_user_data, comic_files
):
    window = paneleo_module.MainWindow()
    window.show()
    window.open_comic(str(comic_files.cbz))
    window.showNormal()
    window.resize(1120, 740)
    window.move(140, 100)
    QTest.qWait(200)
    expected_geometry = window.geometry()
    window.enter_window_fullscreen()
    QTest.qWait(300)
    window.close()
    qt_app.processEvents()

    settings = json.loads(paneleo_module.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert settings["window_maximized"] is False
    assert settings["window_geometry"]

    restored = paneleo_module.MainWindow()
    restored.show()
    QTest.qWait(250)
    _assert_rect_close(restored.geometry(), expected_geometry)
    restored.close()
    qt_app.processEvents()


def test_closing_fullscreen_preserves_previous_maximized_state(
    qt_app, paneleo_module, clean_user_data, comic_files
):
    window = paneleo_module.MainWindow()
    window.showMaximized()
    window.open_comic(str(comic_files.cbz))
    QTest.qWait(300)
    assert window.isMaximized()
    window.enter_window_fullscreen()
    QTest.qWait(300)
    window.close()
    qt_app.processEvents()

    settings = json.loads(paneleo_module.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert settings["window_maximized"] is True

    restored = paneleo_module.MainWindow()
    if restored.settings.get("window_maximized", False):
        restored.showMaximized()
    else:
        restored.show()
    QTest.qWait(300)
    assert restored.isMaximized()
    restored.close()
    qt_app.processEvents()
