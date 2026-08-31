import zipfile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


def _assert_rendered(reader, expected_pages):
    assert len(reader.pages) == expected_pages
    assert reader.page_selector.count() == expected_pages
    pixmap = reader.image_label.pixmap()
    assert pixmap is not None
    assert not pixmap.isNull()
    assert pixmap.width() > 0 and pixmap.height() > 0


def test_main_window_initializes_without_crashing(main_window, paneleo_module):
    assert main_window.windowTitle() == "Paneleo 2.0.0-beta8.4"
    assert main_window.pages.count() == 7
    assert main_window.pages.currentIndex() == paneleo_module.MainWindow.HOME
    assert main_window.isVisible()


def test_cbz_opens_and_pages_render(main_window, comic_files):
    main_window.open_comic(str(comic_files.cbz))
    QTest.qWait(120)
    _assert_rendered(main_window.reader, 3)


def test_pdf_opens_and_pages_render(main_window, comic_files):
    main_window.open_comic(str(comic_files.pdf))
    QTest.qWait(120)
    _assert_rendered(main_window.reader, 2)


def test_cbr_extraction_and_rendering_when_7zip_is_available(main_window, comic_files):
    if comic_files.cbr is None:
        pytest.skip("7-Zip is not installed; Paneleo requires it for CBR files")
    main_window.open_comic(str(comic_files.cbr))
    QTest.qWait(150)
    _assert_rendered(main_window.reader, 3)


def test_click_to_turn_navigation(main_window, comic_files):
    main_window.open_comic(str(comic_files.cbz))
    QTest.qWait(100)
    assert main_window.reader.page_index == 0
    main_window.reader.on_page_side_clicked("right")
    assert main_window.reader.page_index == 1
    main_window.reader.set_manga(True)
    main_window.reader.on_page_side_clicked("left")
    assert main_window.reader.page_index == 0


def test_keyboard_arrow_navigation(main_window, comic_files):
    main_window.open_comic(str(comic_files.cbz))
    main_window.reader.setFocus()
    QTest.keyClick(main_window.reader, Qt.Key.Key_Right)
    QTest.qWait(60)
    assert main_window.reader.page_index == 1
    QTest.keyClick(main_window.reader, Qt.Key.Key_Left)
    QTest.qWait(60)
    assert main_window.reader.page_index == 0


def test_unsafe_cbz_path_is_rejected(paneleo_module, comic_files, tmp_path):
    archive_path = tmp_path / "unsafe.cbz"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(comic_files.pages[0], "../escape.png")
    extraction_root = tmp_path / "extract"
    extraction_root.mkdir()
    with pytest.raises(RuntimeError, match="unsafe file path"):
        paneleo_module.extract_cbz_safely(archive_path, extraction_root)
    assert not (tmp_path / "escape.png").exists()
