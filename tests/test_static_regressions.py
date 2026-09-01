import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"


def _source_and_tree():
    source = APP_PATH.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(APP_PATH))


def test_source_parses_and_compiles():
    source, tree = _source_and_tree()
    compile(tree, str(APP_PATH), "exec")
    assert 'APP_VERSION = "0.1.0-beta.1"' in source


def test_expected_application_structure_is_present():
    _, tree = _source_and_tree()
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {"ReaderWidget", "SafeWebEnginePage", "BrowserWidget", "MainWindow"} <= classes
    assert "main" in functions


def test_fullscreen_regression_markers_are_present():
    source, _ = _source_and_tree()
    required = (
        "home_fullscreen_btn",
        "library_fullscreen_btn",
        "Qt.Key.Key_F11",
        "Qt.ShortcutContext.ApplicationShortcut",
        "self.reader_bar.setVisible(not on)",
        "self.reader_footer.setVisible(not on)",
        "self.reader_control_strip.setVisible(on)",
        "self.fullscreen_zoom_out",
        "self.fullscreen_fit",
        "self.fullscreen_zoom_in",
        "self._fullscreen_restore_maximized = self.isMaximized()",
        "self._fullscreen_restore_geometry = self.saveGeometry()",
        "self.restoreGeometry(self._fullscreen_restore_geometry)",
        "QTimer.singleShot(0, self.render_page)",
    )
    for marker in required:
        assert marker in source


def test_click_keyboard_and_page_selector_regression_markers_are_present():
    source, _ = _source_and_tree()
    for marker in (
        "pageSideClicked",
        "on_page_side_clicked",
        "Qt.Key.Key_Right",
        "Qt.Key.Key_Left",
        "self.page_selector.currentIndexChanged.connect(self.on_page_selected)",
    ):
        assert marker in source


def test_no_dangerous_dynamic_execution_or_archive_extractall():
    _, tree = _source_and_tree()
    builtin_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    attribute_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "eval" not in builtin_calls
    assert "exec" not in builtin_calls
    assert "extractall" not in attribute_calls


def test_subprocesses_do_not_enable_shell_execution():
    _, tree = _source_and_tree()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "subprocess":
            continue
        shell_keywords = [keyword for keyword in node.keywords if keyword.arg == "shell"]
        assert not any(
            isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            for keyword in shell_keywords
        )


def test_chromium_sandbox_is_not_disabled():
    source, _ = _source_and_tree()
    assert "--no-sandbox" not in source
    assert 'os.environ.pop("QTWEBENGINE_DISABLE_SANDBOX", None)' in source


def test_cover_caches_remain_bounded():
    source, _ = _source_and_tree()
    assert "MAX_BATCAVE_COVER_CACHE_ITEMS = 160" in source
    assert "while len(self._cover_pixmaps) > 32" in source


def test_launch_and_build_scripts_validate_the_project_environment():
    run_script = (PROJECT_ROOT / "RUN.bat").read_text(encoding="utf-8")
    build_script = (PROJECT_ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8")
    for script in (run_script, build_script):
        assert "import PySide6; import pymupdf" in script
        assert "sys.prefix != sys.base_prefix" in script
    assert "Recreating the generated .venv environment safely" in run_script
    assert "PYTHONNOUSERSITE=1" in build_script
