import os
import sys
import json
import shutil
import zipfile
import tempfile
import subprocess
import hashlib
import time
import math
import re
import html as html_lib
from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt, QSize, QUrl, Signal, QEvent, QTimer, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QAction, QPixmap, QImage, QImageReader, QIcon, QDesktopServices, QKeySequence, QShortcut, QCursor, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QListWidget, QListWidgetItem, QMessageBox,
    QStackedWidget, QScrollArea, QComboBox, QLineEdit, QProgressBar,
    QSizePolicy, QFrame, QInputDialog, QMenu
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEnginePage, QWebEngineSettings

try:
    import pymupdf as fitz
except Exception:
    try:
        import fitz  # legacy PyMuPDF import
    except Exception:
        fitz = None

APP_NAME = "Paneleo"
APP_VERSION = "2.0.0-beta8.4"
BATCAVE_URL = "https://batcave.biz/"
SUPPORTED = {".cbz", ".cbr", ".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

# Security limits. Comic files and imported backups are untrusted input.
BATCAVE_HOSTS = {"batcave.biz", "www.batcave.biz"}
MAX_JSON_FILE_BYTES = 20 * 1024 * 1024
MAX_BACKUP_FILE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_IMAGES = 5000
MAX_ARCHIVE_MEMBERS = 10000
MAX_PDF_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 300 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 1000.0
MAX_IMAGE_FILE_BYTES = 150 * 1024 * 1024
MAX_IMAGE_PIXELS = 60_000_000
MAX_IMAGE_DIMENSION = 14_000
MAX_BACKUP_MAP_ITEMS = 50_000
MAX_COVER_HTML_BYTES = 6 * 1024 * 1024
MAX_COVER_IMAGE_BYTES = 8 * 1024 * 1024


def is_allowed_batcave_url(url):
    """True only for HTTPS navigation to the BatCave host we embed."""
    try:
        q = url if isinstance(url, QUrl) else QUrl(str(url or ""))
        return bool(q.isValid() and q.scheme().lower() == "https" and q.host().lower() in BATCAVE_HOSTS)
    except Exception:
        return False




def is_allowed_batcave_asset_url(url):
    """Allow HTTPS image assets only from BatCave itself or its subdomains."""
    try:
        q = url if isinstance(url, QUrl) else QUrl(str(url or ""))
        host = q.host().lower().rstrip(".")
        return bool(
            q.isValid()
            and q.scheme().lower() == "https"
            and (host == "batcave.biz" or host.endswith(".batcave.biz"))
            and not q.userInfo()
        )
    except Exception:
        return False


def safe_batcave_url(url, fallback=BATCAVE_URL):
    if not is_allowed_batcave_url(url):
        return fallback
    return url.toString() if isinstance(url, QUrl) else str(url)


def is_batcave_reader_url(url):
    """True only when the live embedded URL is an actual BatCave reader page.

    Reader titles/state can remain stale for a moment during navigation.  The
    sidebar must never hide merely because that stale state still says Reader
    Mode while the browser has already navigated to the BatCave homepage.
    """
    try:
        q = url if isinstance(url, QUrl) else QUrl(str(url or ""))
        if not is_allowed_batcave_url(q):
            return False
        path = (q.path() or "").rstrip("/").lower()
        return path == "/reader" or path.startswith("/reader/")
    except Exception:
        return False


def _validate_image_dimensions(width, height):
    try:
        width, height = int(width), int(height)
    except Exception:
        return False
    return (0 < width <= MAX_IMAGE_DIMENSION and 0 < height <= MAX_IMAGE_DIMENSION
            and width * height <= MAX_IMAGE_PIXELS)


def safe_load_qimage(path):
    """Decode an image only after checking encoded size and declared dimensions."""
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_FILE_BYTES:
            return QImage()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and not _validate_image_dimensions(size.width(), size.height()):
            return QImage()
        image = reader.read()
        if image.isNull() or not _validate_image_dimensions(image.width(), image.height()):
            return QImage()
        return image
    except Exception:
        return QImage()


def safe_qimage_from_bytes(data):
    try:
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_IMAGE_FILE_BYTES:
            return QImage()
        buf = QBuffer()
        buf.setData(QByteArray(bytes(data)))
        if not buf.open(QIODevice.OpenModeFlag.ReadOnly):
            return QImage()
        reader = QImageReader(buf)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and not _validate_image_dimensions(size.width(), size.height()):
            return QImage()
        image = reader.read()
        if image.isNull() or not _validate_image_dimensions(image.width(), image.height()):
            return QImage()
        return image
    except Exception:
        return QImage()


def _safe_pdf_scale(page, requested):
    """Clamp PDF rasterization so a hostile page cannot request enormous bitmaps."""
    try:
        requested = max(0.05, float(requested))
        rect = page.rect
        w, h = abs(float(rect.width)), abs(float(rect.height))
        if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
            raise ValueError("Invalid PDF page dimensions")
        scale = requested
        if w * h * scale * scale > MAX_IMAGE_PIXELS:
            scale = min(scale, math.sqrt(MAX_IMAGE_PIXELS / (w * h)))
        scale = min(scale, MAX_IMAGE_DIMENSION / w, MAX_IMAGE_DIMENSION / h)
        if scale <= 0 or not math.isfinite(scale):
            raise ValueError("PDF page is too large to render safely")
        return scale
    except Exception as exc:
        raise RuntimeError("PDF page dimensions are unsafe or invalid.") from exc


def _zip_image_infos(zf):
    all_infos = zf.infolist()
    if len(all_infos) > MAX_ARCHIVE_MEMBERS:
        raise RuntimeError("This CBZ contains too many archive members.")
    infos = []
    total = 0
    for info in all_infos:
        if info.is_dir():
            continue
        name = str(info.filename or "").replace("\\", "/")
        pp = PurePosixPath(name)
        if pp.is_absolute() or ".." in pp.parts:
            raise RuntimeError("This CBZ contains an unsafe file path.")
        # Unix symlink bit in ZIP metadata. Symlinks are never needed for comics.
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise RuntimeError("This CBZ contains a symbolic link and was blocked.")
        if info.flag_bits & 0x1:
            raise RuntimeError("Encrypted CBZ archives are not supported.")
        if Path(name).suffix.lower() not in IMAGE_EXTS:
            continue
        if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RuntimeError("A comic page is too large to extract safely.")
        total += int(info.file_size)
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise RuntimeError("This comic expands beyond the safe extraction limit.")
        packed = max(1, int(info.compress_size or 0))
        if info.file_size > 10 * 1024 * 1024 and (info.file_size / packed) > MAX_ARCHIVE_COMPRESSION_RATIO:
            raise RuntimeError("This CBZ has a suspicious compression ratio and was blocked.")
        infos.append(info)
        if len(infos) > MAX_ARCHIVE_IMAGES:
            raise RuntimeError("This comic contains too many image files.")
    if not infos:
        raise RuntimeError("No readable pages were found in this comic.")
    return sorted(infos, key=lambda i: natural_key(i.filename))


def _copy_stream_bounded(src, dst, limit):
    written = 0
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            break
        written += len(chunk)
        if written > limit:
            raise RuntimeError("Archive member exceeded its declared safe size.")
        dst.write(chunk)
    return written


def extract_cbz_safely(file_path, temp_dir):
    file_path = Path(file_path)
    if file_path.stat().st_size > MAX_ARCHIVE_FILE_BYTES:
        raise RuntimeError("This CBZ is larger than the safe archive limit.")
    out_dir = Path(temp_dir) / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    with zipfile.ZipFile(file_path, "r") as zf:
        infos = _zip_image_infos(zf)
        for index, info in enumerate(infos):
            ext = Path(info.filename).suffix.lower()
            out = out_dir / f"{index:05d}{ext}"
            with zf.open(info, "r") as src, open(out, "wb") as dst:
                _copy_stream_bounded(src, dst, min(MAX_ARCHIVE_MEMBER_BYTES, int(info.file_size) + 1))
            pages.append(out)
    return pages


def _parse_7z_listing(text):
    entries, current = [], {}
    for line in (text or "").splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            if current:
                entries.append(current); current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key.strip()] = value.strip()
    if current:
        entries.append(current)
    return entries


def validate_cbr_before_extract(seven, file_path):
    if Path(file_path).stat().st_size > MAX_ARCHIVE_FILE_BYTES:
        raise RuntimeError("This CBR is larger than the safe archive limit.")
    try:
        result = subprocess.run([seven, "l", "-slt", str(file_path)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=45,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while inspecting this CBR.") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError("7-Zip could not inspect this CBR safely.")
    entries = _parse_7z_listing(result.stdout)
    if len(entries) > MAX_ARCHIVE_MEMBERS + 50:
        raise RuntimeError("This CBR contains too many archive members.")
    count = total = 0
    for entry in entries:
        name = entry.get("Path", "")
        if Path(name).suffix.lower() not in IMAGE_EXTS:
            continue
        try:
            size = int(entry.get("Size", "0") or 0)
            packed = int(entry.get("Packed Size", "0") or 0)
        except Exception:
            raise RuntimeError("CBR metadata is invalid.")
        if size < 0 or size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RuntimeError("A comic page is too large to extract safely.")
        total += size; count += 1
        if count > MAX_ARCHIVE_IMAGES or total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise RuntimeError("This CBR exceeds the safe extraction limits.")
        if size > 10 * 1024 * 1024 and packed > 0 and (size / packed) > MAX_ARCHIVE_COMPRESSION_RATIO:
            raise RuntimeError("This CBR has a suspicious compression ratio and was blocked.")
    if count < 1:
        raise RuntimeError("No readable pages were found in this comic.")


def extract_cbr_safely(file_path, temp_dir):
    seven = find_7zip()
    if not seven:
        raise RuntimeError("CBR files need 7-Zip. Install 7-Zip, then reopen Paneleo.")
    validate_cbr_before_extract(seven, file_path)
    out_dir = Path(temp_dir) / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    patterns = [f"*{ext}" for ext in sorted(IMAGE_EXTS)]
    try:
        result = subprocess.run([seven, "e", "-y", "-aou", f"-o{out_dir}", str(file_path), *patterns],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while extracting this CBR.") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError("7-Zip could not open this CBR file.")
    pages = sorted([p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=natural_key)
    if not pages or len(pages) > MAX_ARCHIVE_IMAGES:
        raise RuntimeError("No readable pages were found in this comic.")
    total = 0
    for page in pages:
        size = page.stat().st_size
        if size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RuntimeError("An extracted comic page exceeded the safe size limit.")
        total += size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise RuntimeError("Extracted comic data exceeded the safe size limit.")
    return pages



def _safe_text(value, limit=4096, default=""):
    if value is None:
        return default
    try:
        text = str(value)
    except Exception:
        return default
    return text[:limit]


def _safe_int(value, minimum=0, maximum=10_000_000, default=0):
    try:
        return max(minimum, min(maximum, int(value)))
    except Exception:
        return default


def sanitize_settings(settings, strict=False):
    if not isinstance(settings, dict):
        if strict:
            raise ValueError("Backup settings must be an object.")
        return {}
    if len(settings) > 500:
        if strict:
            raise ValueError("Backup contains too many settings.")
    out = {}
    for key in ("comic_dir", "last_local_file"):
        if key in settings:
            out[key] = _safe_text(settings.get(key), 32768)
    for key in ("last_local_page", "last_local_total", "last_local_opened", "recent_cleared_at"):
        if key in settings:
            out[key] = _safe_int(settings.get(key), 0, 2_147_483_647)
    for key in ("sidebar_collapsed", "window_maximized"):
        if key in settings:
            out[key] = bool(settings.get(key))
    geom = settings.get("window_geometry")
    if isinstance(geom, str) and len(geom) <= 10000 and all(c in "0123456789abcdefABCDEF" for c in geom):
        out["window_geometry"] = geom
    fit = settings.get("batcave_fit_mode")
    if fit in ("page", "width", "original"):
        out["batcave_fit_mode"] = fit
    sort_mode = settings.get("reading_sort_mode")
    if sort_mode in ("Recently read", "Title A–Z", "Progress"):
        out["reading_sort_mode"] = sort_mode
    last_url = settings.get("last_batcave_url")
    out["last_batcave_url"] = safe_batcave_url(last_url)
    issue_url = settings.get("last_batcave_issue_url")
    out["last_batcave_issue_url"] = _safe_text(issue_url, 4096) if is_allowed_batcave_url(issue_url) else ""
    if "last_batcave_title" in settings:
        out["last_batcave_title"] = _safe_text(settings.get("last_batcave_title"), 1000)
    expanded = settings.get("reading_list_expanded", {})
    if isinstance(expanded, dict):
        if strict and len(expanded) > 10000:
            raise ValueError("Backup contains too many Reading List state entries.")
        out["reading_list_expanded"] = {
            _safe_text(k, 4096): bool(v)
            for k, v in list(expanded.items())[:10000]
            if isinstance(k, str)
        }
    zoom = settings.get("series_zoom", {})
    if isinstance(zoom, dict):
        if strict and len(zoom) > 10000:
            raise ValueError("Backup contains too many zoom preferences.")
        clean_zoom = {}
        for k, v in list(zoom.items())[:10000]:
            if not isinstance(k, str) or len(k) > 500:
                continue
            try:
                f = max(0.50, min(2.50, round(float(v), 2)))
            except Exception:
                continue
            clean_zoom[k] = f
        out["series_zoom"] = clean_zoom
    return out


def sanitize_progress(progress, strict=False):
    if not isinstance(progress, dict):
        if strict:
            raise ValueError("Backup local progress must be an object.")
        return {}
    if len(progress) > MAX_BACKUP_MAP_ITEMS:
        if strict:
            raise ValueError("Backup contains too many local progress entries.")
    out = {}
    for k, v in list(progress.items())[:MAX_BACKUP_MAP_ITEMS]:
        if not isinstance(k, str) or len(k) > 32768:
            continue
        out[k] = _safe_int(v, 0, 10_000_000)
    return out


def sanitize_batcave_library(library, strict=False):
    if not isinstance(library, dict):
        if strict:
            raise ValueError("Backup reading library must be an object.")
        library = {}
    out = {"saved": {}, "read": {}, "issues": {}, "bookmarks": {}}
    schemas = {
        "saved": ("title", "added", "cover_page_url", "cover_image_url"),
        "read": ("title", "read_at", "automatic", "completed_page", "total_pages"),
        "issues": ("title", "series", "issue", "current_page", "total_pages", "last_opened", "cover_page_url", "cover_image_url"),
        "bookmarks": ("title", "series", "issue", "page", "total_pages", "note", "added"),
    }
    int_fields = {"added", "read_at", "completed_page", "total_pages", "current_page", "last_opened", "page"}
    for section, fields in schemas.items():
        rows = library.get(section, {})
        if not isinstance(rows, dict):
            if strict:
                raise ValueError(f"Backup section '{section}' must be an object.")
            continue
        if len(rows) > MAX_BACKUP_MAP_ITEMS:
            if strict:
                raise ValueError(f"Backup section '{section}' is too large.")
        for raw_key, row in list(rows.items())[:MAX_BACKUP_MAP_ITEMS]:
            if not isinstance(row, dict):
                if strict:
                    raise ValueError(f"Backup section '{section}' contains an invalid row.")
                continue
            fallback = str(raw_key).split("|page:", 1)[0] if section == "bookmarks" else raw_key
            url = row.get("url") or fallback
            if not is_allowed_batcave_url(url):
                if strict:
                    raise ValueError("Backup contains a non-BatCave URL and was blocked.")
                continue
            clean_url = canonical_url(url)
            clean = {"url": clean_url}
            for field in fields:
                if field not in row:
                    continue
                value = row.get(field)
                if field in int_fields:
                    clean[field] = _safe_int(value, 0, 2_147_483_647)
                elif field == "automatic":
                    clean[field] = bool(value)
                elif field == "note":
                    clean[field] = _safe_text(value, 1000)
                elif field in ("title", "series"):
                    clean[field] = _safe_text(value, 1000)
                elif field == "issue":
                    clean[field] = _safe_text(value, 100)
                elif field == "cover_page_url":
                    if is_allowed_batcave_url(value):
                        clean[field] = canonical_url(value)
                elif field == "cover_image_url":
                    if is_allowed_batcave_asset_url(value):
                        clean[field] = QUrl(str(value)).toString()
            if section == "bookmarks":
                page = _safe_int(clean.get("page", 0), 0, 10_000_000)
                key = f"{clean_url}|page:{page}"
            else:
                key = clean_url
            out[section][key] = clean
    return out


def validate_backup_payload(payload):
    if not isinstance(payload, dict) or payload.get("format") not in {"ComicReaderBackup", "PaneleoBackup"}:
        raise ValueError("This file is not a Paneleo/Comic Reader backup.")
    version = _safe_int(payload.get("version", 0), 0, 1000)
    if version != 1:
        raise ValueError("This backup format is not supported by this version.")
    return (
        sanitize_settings(payload.get("settings"), strict=True),
        sanitize_progress(payload.get("progress"), strict=True),
        sanitize_batcave_library(payload.get("batcave_library"), strict=True),
    )


def natural_key(path):
    import re
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", str(path))]


def app_data_dir():
    """Return Paneleo's app-data directory and non-destructively migrate v1 data.

    The old Comic Reader directory is kept untouched as a rollback copy.  On
    first Paneleo launch we copy only the known user-data files/cache so the
    redesign starts with the user's existing progress, Reading List and
    bookmarks.
    """
    base = Path(os.environ.get("APPDATA") or str(Path.home() / ".comic_reader"))
    legacy = base / "OpenAI-ComicReader"
    p = base / "Paneleo"
    p.mkdir(parents=True, exist_ok=True)
    marker = p / ".migration_v2_done"
    if not marker.exists() and legacy.exists():
        try:
            for name in ("settings.json", "progress.json", "batcave_library.json"):
                src = legacy / name
                dst = p / name
                if src.is_file() and not dst.exists():
                    shutil.copy2(src, dst)
            src_cache = legacy / "cover_cache"
            dst_cache = p / "cover_cache"
            if src_cache.is_dir() and not dst_cache.exists():
                shutil.copytree(src_cache, dst_cache, dirs_exist_ok=True)
            marker.write_text("Migrated non-destructively from Comic Reader v1.\n", encoding="utf-8")
        except Exception:
            # Never prevent the app from starting because a legacy migration
            # failed. The old directory remains available for manual recovery.
            pass
    return p


SETTINGS_FILE = app_data_dir() / "settings.json"
PROGRESS_FILE = app_data_dir() / "progress.json"
BATCAVE_LIBRARY_FILE = app_data_dir() / "batcave_library.json"
CACHE_DIR = app_data_dir() / "cover_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BATCAVE_COVER_CACHE_DIR = CACHE_DIR / "batcave"
BATCAVE_COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
MAX_BATCAVE_COVER_CACHE_ITEMS = 160


def load_json(path, default):
    try:
        path = Path(path)
        if path.exists():
            if path.stat().st_size > MAX_JSON_FILE_BYTES:
                return default
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path, data):
    """Best-effort atomic JSON write so a crash is less likely to corrupt user data."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp = Path(str(path) + ".tmp")
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def canonical_url(url):
    try:
        q = QUrl(str(url))
        q.setFragment("")
        text = q.toString().strip()
    except Exception:
        text = str(url or "").strip()
    if text.endswith("/"):
        text = text[:-1]
    return text


def find_7zip():
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    candidates = [
        str(Path(program_files) / "7-Zip" / "7z.exe"),
        str(Path(program_files_x86) / "7-Zip" / "7z.exe"),
        os.environ.get("PANELEO_7ZIP", "") or os.environ.get("COMICREADER_7ZIP", ""),
    ]
    for c in candidates:
        try:
            if c and Path(c).is_file():
                return str(Path(c).resolve())
        except Exception:
            continue
    return None


class ClickableList(QListWidget):
    fileActivated = Signal(str)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.pos())
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                self.fileActivated.emit(path)
        super().mouseDoubleClickEvent(event)


class HorizontalShelf(QListWidget):
    """Horizontal media shelf with mouse-wheel scrolling and no visible bar."""

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar = self.horizontalScrollBar()
            step = max(90, int(bar.pageStep() * 0.32))
            bar.setValue(bar.value() - step if delta > 0 else bar.value() + step)
            event.accept()
            return
        super().wheelEvent(event)


class PageTurnLabel(QLabel):
    """Comic page surface with left/right click zones for local reading."""
    pageSideClicked = Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click the left or right half of the page to turn pages")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.width() > 1:
            side = "left" if event.position().x() < (self.width() / 2.0) else "right"
            self.pageSideClicked.emit(side)
            event.accept()
            return
        super().mousePressEvent(event)


class ReaderWidget(QWidget):
    backRequested = Signal()

    def __init__(self, progress_store, on_progress=None, parent=None):
        super().__init__(parent)
        self.progress_store = progress_store
        self.on_progress = on_progress
        self.current_file = None
        self.temp_dir = None
        self.pages = []
        self.page_index = 0
        self.fit_mode = "page"
        self.zoom_factor = 1.0
        self.rtl = False
        self.pdf_doc = None
        self._page_selector_updating = False
        self._fullscreen_controls_active = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget()
        self.reader_bar = bar
        bar.setObjectName("readerBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 12, 8)

        back = QPushButton("← Library")
        back.clicked.connect(self.close_reader)
        self.title = QLabel("Reader")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setObjectName("readerTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.top_prev_btn = QPushButton("‹")
        self.top_prev_btn.setObjectName("readerNavButton")
        self.top_prev_btn.setFixedWidth(38)
        self.top_prev_btn.setToolTip("Previous page")
        self.top_prev_btn.clicked.connect(self.prev_page)

        self.page_selector = QComboBox()
        self.page_selector.setObjectName("readerPageSelector")
        self.page_selector.setMinimumWidth(118)
        self.page_selector.setToolTip("Current page — choose a page to jump")
        self.page_selector.addItem("Page 0 / 0", 0)
        self.page_selector.currentIndexChanged.connect(self.on_page_selected)

        self.top_next_btn = QPushButton("›")
        self.top_next_btn.setObjectName("readerNavButton")
        self.top_next_btn.setFixedWidth(38)
        self.top_next_btn.setToolTip("Next page")
        self.top_next_btn.clicked.connect(self.next_page)

        self.mode = QComboBox()
        self.mode.addItems(["Fit page", "Fit width", "Actual size"])
        self.mode.currentIndexChanged.connect(self.on_fit_changed)

        self.manga_btn = QPushButton("Manga: Off")
        self.manga_btn.setCheckable(True)
        self.manga_btn.toggled.connect(self.set_manga)

        self.fullscreen_btn = QPushButton("Fullscreen")
        self.fullscreen_btn.setToolTip("Distraction-free fullscreen (F11)")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        bl.addWidget(back)
        bl.addWidget(self.title)
        bl.addWidget(self.top_prev_btn)
        bl.addWidget(self.page_selector)
        bl.addWidget(self.top_next_btn)
        bl.addWidget(self.mode)
        bl.addWidget(self.manga_btn)
        bl.addWidget(self.fullscreen_btn)
        root.addWidget(bar)

        # Match BatCave Reader Mode with a compact native control strip. The
        # strip keeps a fixed height, so collapsing the toolbar never shifts
        # the comic or changes the Fit Page viewport.
        self.reader_control_strip = QFrame()
        self.reader_control_strip.setObjectName("readerControlStrip")
        self.reader_control_strip.setFixedHeight(52)
        self.reader_control_strip.hide()
        root.addWidget(self.reader_control_strip)

        self.fullscreen_nav = QFrame(self.reader_control_strip)
        self.fullscreen_nav.setObjectName("readerNav")
        self.fullscreen_nav.setAttribute(Qt.WA_NoMousePropagation, True)
        fn = QHBoxLayout(self.fullscreen_nav)
        fn.setContentsMargins(5, 5, 5, 5)
        fn.setSpacing(4)

        self.fullscreen_back = QPushButton("←")
        self.fullscreen_back.setToolTip("Back to Local Library")
        self.fullscreen_prev = QPushButton("‹")
        self.fullscreen_prev.setToolTip("Previous page")
        self.fullscreen_page = QComboBox()
        self.fullscreen_page.setObjectName("readerPageSelector")
        self.fullscreen_page.setFixedHeight(36)
        self.fullscreen_page.setMinimumWidth(118)
        self.fullscreen_page.setToolTip("Current page — choose a page to jump")
        self.fullscreen_page.addItem("Page 0 / 0", 0)
        self.fullscreen_next = QPushButton("›")
        self.fullscreen_next.setToolTip("Next page")
        self.fullscreen_zoom_out = QPushButton("−")
        self.fullscreen_zoom_out.setToolTip("Zoom out")
        self.fullscreen_fit = QPushButton("Fit")
        self.fullscreen_fit.setToolTip("Fit page / reset zoom")
        self.fullscreen_zoom_in = QPushButton("+")
        self.fullscreen_zoom_in.setToolTip("Zoom in")
        self.fullscreen_exit = QPushButton("☰")
        self.fullscreen_exit.setToolTip("Exit fullscreen (Esc)")

        for button in (
            self.fullscreen_back, self.fullscreen_prev, self.fullscreen_next,
            self.fullscreen_zoom_out, self.fullscreen_fit,
            self.fullscreen_zoom_in, self.fullscreen_exit,
        ):
            button.setObjectName("readerNavButton")
            button.setFixedSize(40 if button is not self.fullscreen_fit else 64, 36)
            button.setAttribute(Qt.WA_NoMousePropagation, True)
            button.clicked.connect(self._show_fullscreen_controls)

        self.fullscreen_back.clicked.connect(self.close_reader)
        self.fullscreen_prev.clicked.connect(self.prev_page)
        self.fullscreen_page.currentIndexChanged.connect(self.on_page_selected)
        self.fullscreen_page.activated.connect(self._show_fullscreen_controls)
        self.fullscreen_next.clicked.connect(self.next_page)
        self.fullscreen_zoom_out.clicked.connect(lambda: self.adjust_zoom(-0.08))
        self.fullscreen_fit.clicked.connect(self.fit_page)
        self.fullscreen_zoom_in.clicked.connect(lambda: self.adjust_zoom(0.08))
        self.fullscreen_exit.clicked.connect(self.toggle_fullscreen)

        for control in (
            self.fullscreen_back, self.fullscreen_prev, self.fullscreen_page,
            self.fullscreen_next, self.fullscreen_zoom_out,
            self.fullscreen_fit, self.fullscreen_zoom_in, self.fullscreen_exit,
        ):
            fn.addWidget(control)

        self.fullscreen_nav.adjustSize()
        self.fullscreen_nav.hide()
        self.fullscreen_nav.raise_()

        self.fullscreen_handle = QPushButton("☰", self.reader_control_strip)
        self.fullscreen_handle.setObjectName("readerHandle")
        self.fullscreen_handle.setFixedSize(34, 30)
        self.fullscreen_handle.setToolTip("Show reader controls")
        self.fullscreen_handle.clicked.connect(self._show_fullscreen_controls)
        self.fullscreen_handle.hide()
        self.fullscreen_handle.raise_()

        self._fullscreen_nav_last_activity = time.monotonic()
        self._fullscreen_nav_autohide = QTimer(self)
        self._fullscreen_nav_autohide.setInterval(150)
        self._fullscreen_nav_autohide.timeout.connect(self._update_fullscreen_nav_autohide)
        self._fullscreen_nav_autohide.start()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.image_label = PageTurnLabel("Open a comic to begin")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(400, 400)
        self.image_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.image_label.pageSideClicked.connect(self.on_page_side_clicked)
        self.scroll.setWidget(self.image_label)
        root.addWidget(self.scroll, 1)

        footer = QWidget()
        self.reader_footer = footer
        footer.setObjectName("readerFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 6, 12, 6)
        self.prev_btn = QPushButton("◀ Previous")
        self.next_btn = QPushButton("Next ▶")
        self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn.clicked.connect(self.next_page)
        self.page_label = QLabel("0 / 0")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(280)
        fl.addWidget(self.prev_btn)
        fl.addStretch(1)
        fl.addWidget(self.page_label)
        fl.addWidget(self.progress)
        fl.addStretch(1)
        fl.addWidget(self.next_btn)
        root.addWidget(footer)

    def toggle_fullscreen(self):
        w = self.window()
        if hasattr(w, "toggle_window_fullscreen"):
            w.toggle_window_fullscreen()
        elif w.isFullScreen():
            w.showNormal()
        else:
            w.showFullScreen()

    def set_distraction_free(self, on):
        """Use BatCave-style compact controls while the shell is fullscreen."""
        on = bool(on)
        self._fullscreen_controls_active = on
        if hasattr(self, "reader_bar"):
            self.reader_bar.setVisible(not on)
        if hasattr(self, "reader_footer"):
            self.reader_footer.setVisible(not on)
        if hasattr(self, "reader_control_strip"):
            self.reader_control_strip.setVisible(on)
        if on:
            self._show_fullscreen_controls()
        else:
            self.fullscreen_nav.hide()
            self.fullscreen_handle.hide()
        if hasattr(self, "fullscreen_btn"):
            self.fullscreen_btn.setText("Exit fullscreen" if on else "Fullscreen")
        # Re-fit the current page after the fullscreen/window layout has settled.
        if self.pages:
            QTimer.singleShot(0, self.render_page)
        if on:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_fullscreen_controls(self, *_):
        if not self._fullscreen_controls_active:
            return
        self._fullscreen_nav_last_activity = time.monotonic()
        self.fullscreen_nav.adjustSize()
        self.fullscreen_nav.move(8, 4)
        self.fullscreen_handle.move(8, 9)
        self.fullscreen_handle.hide()
        self.fullscreen_nav.show()
        self.fullscreen_nav.raise_()

    def _update_fullscreen_nav_autohide(self):
        if not self._fullscreen_controls_active or not self.fullscreen_nav.isVisible():
            return
        now = time.monotonic()
        if self.fullscreen_nav.underMouse():
            self._fullscreen_nav_last_activity = now
            return
        if now - self._fullscreen_nav_last_activity >= 2.4:
            self.fullscreen_nav.hide()
            self.fullscreen_handle.show()
            self.fullscreen_handle.raise_()

    def adjust_zoom(self, delta):
        new_factor = max(0.50, min(2.50, round(self.zoom_factor + delta, 2)))
        if new_factor == self.zoom_factor:
            return
        self.zoom_factor = new_factor
        self.fullscreen_fit.setText(f"{int(self.zoom_factor * 100)}%")
        self.fullscreen_fit.setToolTip("Fit page / reset zoom")
        self.render_page()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def reset_zoom(self):
        self.zoom_factor = 1.0
        self.fullscreen_fit.setText("Fit")
        self.render_page()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def fit_page(self):
        self.fit_mode = "page"
        if self.mode.currentIndex() != 0:
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(0)
            self.mode.blockSignals(False)
        self.reset_zoom()

    def set_manga(self, on):
        self.rtl = on
        self.manga_btn.setText("Manga: On" if on else "Manga: Off")
        direction = "Left side = next · Right side = previous" if on else "Left side = previous · Right side = next"
        self.image_label.setToolTip(f"Click page to turn · {direction}")

    def on_page_side_clicked(self, side):
        if not self.pages:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.rtl:
            self.next_page() if side == "left" else self.prev_page()
        else:
            self.prev_page() if side == "left" else self.next_page()

    def _sync_page_selector(self):
        total = len(self.pages)
        self._page_selector_updating = True
        try:
            for selector in (self.page_selector, self.fullscreen_page):
                selector.blockSignals(True)
                if total <= 0:
                    selector.clear()
                    selector.addItem("Page 0 / 0", 0)
                    selector.setEnabled(False)
                else:
                    if selector.count() != total:
                        selector.clear()
                        for n in range(1, total + 1):
                            selector.addItem(f"Page {n} / {total}", n)
                    selector.setEnabled(True)
                    selector.setCurrentIndex(max(0, min(self.page_index, total - 1)))
                    selector.setToolTip(
                        f"Page {self.page_index + 1} of {total} — select a page to jump"
                    )
                selector.blockSignals(False)
        finally:
            self._page_selector_updating = False

    def on_page_selected(self, index):
        if self._page_selector_updating or not self.pages or index < 0:
            return
        selector = self.sender()
        if not isinstance(selector, QComboBox):
            selector = self.page_selector
        value = selector.itemData(index)
        try:
            page_number = int(value)
        except Exception:
            return
        new_index = page_number - 1
        if 0 <= new_index < len(self.pages) and new_index != self.page_index:
            self.page_index = new_index
            self.render_page()
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def on_fit_changed(self, idx):
        self.fit_mode = ["page", "width", "actual"][idx]
        self.render_page()

    def open_file(self, file_path):
        self.cleanup()
        self.current_file = str(file_path)
        self.zoom_factor = 1.0
        self.fullscreen_fit.setText("Fit")
        self.title.setText(Path(file_path).stem)
        self.temp_dir = Path(tempfile.mkdtemp(prefix="comic_reader_"))
        ext = Path(file_path).suffix.lower()

        try:
            if ext == ".cbz":
                self.pages = extract_cbz_safely(file_path, self.temp_dir)
            elif ext == ".cbr":
                self.pages = extract_cbr_safely(file_path, self.temp_dir)
            elif ext == ".pdf":
                if fitz is None:
                    raise RuntimeError("PDF support is unavailable. Run INSTALL.bat again.")
                if Path(file_path).stat().st_size > MAX_PDF_FILE_BYTES:
                    raise RuntimeError("This PDF is larger than the safe file-size limit.")
                self.pdf_doc = fitz.open(file_path)
                self.pages = list(range(len(self.pdf_doc)))
            else:
                raise RuntimeError("Unsupported file type.")

            if not self.pages:
                raise RuntimeError("No readable pages were found in this comic.")

            saved = self.progress_store.get(self.current_file, 0)
            self.page_index = max(0, min(int(saved), len(self.pages) - 1))
            self._sync_page_selector()
            self.render_page()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception as e:
            QMessageBox.critical(self, "Could not open comic", str(e))
            self.close_reader()

    def _current_qimage(self):
        if self.pdf_doc is not None:
            page = self.pdf_doc[self.page_index]
            scale = _safe_pdf_scale(page, 1.8)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            fmt = QImage.Format.Format_RGB888 if pix.n == 3 else QImage.Format.Format_RGBA8888
            return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
        return safe_load_qimage(self.pages[self.page_index])

    def render_page(self):
        if not self.pages:
            return
        try:
            img = self._current_qimage()
        except Exception:
            self.image_label.setText("Could not safely render this page")
            return
        if img.isNull():
            self.image_label.setText("Could not render this page")
            return

        viewport = self.scroll.viewport().size()
        if self.fit_mode == "width":
            target_w = max(200, viewport.width() - 24)
            pix = QPixmap.fromImage(img).scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
        elif self.fit_mode == "actual":
            pix = QPixmap.fromImage(img)
        else:
            target = QSize(max(200, viewport.width() - 24), max(200, viewport.height() - 24))
            pix = QPixmap.fromImage(img).scaled(
                target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )

        if self.zoom_factor != 1.0 and not pix.isNull():
            zoomed = QSize(
                max(1, round(pix.width() * self.zoom_factor)),
                max(1, round(pix.height() * self.zoom_factor)),
            )
            pix = QPixmap.fromImage(img).scaled(
                zoomed, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )

        self.image_label.setPixmap(pix)
        self.image_label.resize(pix.size())
        self.page_label.setText(f"{self.page_index + 1} / {len(self.pages)}")
        self._sync_page_selector()
        self.progress.setMaximum(max(1, len(self.pages)))
        self.progress.setValue(self.page_index + 1)
        can_prev = self.page_index > 0 if not self.rtl else self.page_index < len(self.pages) - 1
        can_next = self.page_index < len(self.pages) - 1 if not self.rtl else self.page_index > 0
        self.prev_btn.setEnabled(can_prev)
        self.next_btn.setEnabled(can_next)
        self.top_prev_btn.setEnabled(can_prev)
        self.top_next_btn.setEnabled(can_next)
        self.fullscreen_prev.setEnabled(can_prev)
        self.fullscreen_next.setEnabled(can_next)
        if self.current_file:
            self.progress_store[self.current_file] = self.page_index
            save_json(PROGRESS_FILE, self.progress_store)
            if self.on_progress:
                self.on_progress(self.current_file, self.page_index, len(self.pages))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "fullscreen_nav"):
            self.fullscreen_nav.adjustSize()
            self.fullscreen_nav.move(8, 4)
            self.fullscreen_nav.raise_()
        if hasattr(self, "fullscreen_handle"):
            self.fullscreen_handle.move(8, 9)
            self.fullscreen_handle.raise_()
        if self.fit_mode in ("page", "width"):
            self.render_page()

    def next_page(self):
        if not self.pages:
            return
        delta = -1 if self.rtl else 1
        new = self.page_index + delta
        if 0 <= new < len(self.pages):
            self.page_index = new
            self.render_page()
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def prev_page(self):
        if not self.pages:
            return
        delta = 1 if self.rtl else -1
        new = self.page_index + delta
        if 0 <= new < len(self.pages):
            self.page_index = new
            self.render_page()
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() in (
            Qt.Key.Key_Plus, Qt.Key.Key_Equal
        ):
            self.adjust_zoom(0.08)
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Minus:
            self.adjust_zoom(-0.08)
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_0:
            self.fit_page()
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_PageDown):
            self.next_page()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.prev_page()
        elif event.key() == Qt.Key.Key_Escape and self.window().isFullScreen():
            w = self.window()
            if hasattr(w, "exit_window_fullscreen"):
                w.exit_window_fullscreen()
            else:
                w.showNormal()
        else:
            super().keyPressEvent(event)

    def close_reader(self):
        self.cleanup()
        self.backRequested.emit()

    def cleanup(self):
        if self.pdf_doc is not None:
            try:
                self.pdf_doc.close()
            except Exception:
                pass
        self.pdf_doc = None
        self.pages = []
        self.page_index = 0
        self._sync_page_selector()
        self.page_label.setText("0 / 0")
        self.progress.setMaximum(1)
        self.progress.setValue(0)
        self.image_label.clear()
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = None


class SafeWebEnginePage(QWebEnginePage):
    """Restrict the embedded browser to BatCave and deny local-file uploads."""
    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.toString() == "about:blank":
            return True
        if is_allowed_batcave_url(url):
            return True
        if is_main_frame and nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked and url.scheme().lower() in ("http", "https"):
            QDesktopServices.openUrl(url)
        return False


class BrowserWidget(QWidget):
    urlSaved = Signal(str)
    issueChanged = Signal(str, str)
    readingModeChanged = Signal(bool)
    fitModeChanged = Signal(str)
    saveToggleRequested = Signal(str, str)
    readToggleRequested = Signal(str, str)
    readingListRequested = Signal()
    bookmarkRequested = Signal(str, str, int, int)
    autoReadDetected = Signal(str, str, int, int)
    issueProgress = Signal(str, str, int, int)
    zoomChanged = Signal(str, str, float)

    def __init__(self, start_url=BATCAVE_URL, parent=None):
        super().__init__(parent)
        self.reading_mode = False
        self._current_title = ""
        self.clean_view = True
        self.zoom_factor = 1.0  # comic-image zoom only; web page stays at 100%
        self.fit_mode = "page"
        self.saved_urls = set()
        self.read_urls = set()
        self._pending_resume_page = 0
        self._pending_resume_source_url = ""
        self._pending_resume_attempts = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.controls = QWidget()
        self.controls.setObjectName("browserBar")
        cl = QHBoxLayout(self.controls)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(5)
        self.back = QPushButton("←")
        self.forward = QPushButton("→")
        self.reload = QPushButton("↻")
        for btn in (self.back, self.forward, self.reload):
            btn.setObjectName("browserIconButton")
            btn.setFixedWidth(34)
        self.home = QPushButton("BatCave")
        self.home.setObjectName("browserHomeButton")
        self.clean = QPushButton("Clean")
        self.clean.setObjectName("browserToolButton")
        self.clean.setCheckable(True)
        self.clean.setChecked(True)
        self.save_item = QPushButton("☆ Save")
        self.save_item.setObjectName("browserToolButton")
        self.save_item.setToolTip("Add/remove this BatCave page from your Reading List")

        self.issue_tools = QFrame()
        self.issue_tools.setObjectName("issueTools")
        it = QHBoxLayout(self.issue_tools)
        it.setContentsMargins(5, 0, 5, 0)
        it.setSpacing(5)
        self.mark_read = QPushButton("✓ Read")
        self.mark_read.setObjectName("browserToolButton")
        self.mark_read.setToolTip("Mark the current issue read/unread")
        self.fit = QComboBox()
        self.fit.addItems(["Fit Page", "Fit Width", "Original"])
        self.fit.setToolTip("How comic pages are scaled in Clean View")
        self.reader_mode_btn = QPushButton("Reader")
        self.reader_mode_btn.setObjectName("browserToolButton")
        self.zoom_out = QPushButton("−")
        self.zoom_reset = QPushButton("100%")
        self.zoom_in = QPushButton("+")
        for btn in (self.zoom_out, self.zoom_reset, self.zoom_in):
            btn.setObjectName("browserIconButton")
        it.addWidget(self.mark_read)
        it.addWidget(self.fit)
        it.addWidget(self.reader_mode_btn)
        it.addWidget(self.zoom_out)
        it.addWidget(self.zoom_reset)
        it.addWidget(self.zoom_in)
        self.issue_tools.hide()

        self.external = QPushButton("↗")
        self.external.setObjectName("browserIconButton")
        self.external.setToolTip("Open current page in your normal browser")
        self.status = QLabel("BatCave")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("browserStatus")
        cl.addWidget(self.back)
        cl.addWidget(self.forward)
        cl.addWidget(self.reload)
        cl.addWidget(self.home)
        cl.addSpacing(5)
        cl.addWidget(self.clean)
        cl.addWidget(self.save_item)
        cl.addWidget(self.issue_tools)
        cl.addWidget(self.status, 1)
        cl.addWidget(self.external)
        root.addWidget(self.controls)

        # Keep the reader controls in a permanent native strip outside the
        # QWebEngineView. Placing the reveal handle and floating controls above
        # the webview means their clicks can never fall through to
        # BatCave's click-to-turn/scroll handlers. The strip stays the same
        # height while reading, so hiding/showing controls cannot move the page.
        self.reader_control_strip = QFrame()
        self.reader_control_strip.setObjectName("readerControlStrip")
        self.reader_control_strip.setFixedHeight(52)
        self.reader_control_strip.hide()
        root.addWidget(self.reader_control_strip)

        self.web = QWebEngineView()
        self.web.setPage(SafeWebEnginePage(self.web))
        web_settings = self.web.settings()
        for attr_name in (
            "LocalContentCanAccessFileUrls",
            "LocalContentCanAccessRemoteUrls",
            "AllowRunningInsecureContent",
            "JavascriptCanAccessClipboard",
            "JavascriptCanOpenWindows",
            "PluginsEnabled",
            "HyperlinkAuditingEnabled",
        ):
            attr = getattr(QWebEngineSettings.WebAttribute, attr_name, None)
            if attr is not None:
                web_settings.setAttribute(attr, False)
        try:
            self.web.page().permissionRequested.connect(lambda permission: permission.deny())
        except Exception:
            pass
        try:
            self.web.page().profile().downloadRequested.connect(lambda item: item.cancel())
        except Exception:
            pass
        self._install_early_site_patches()
        self.web.setUrl(QUrl(safe_batcave_url(start_url)))
        self.web.setZoomFactor(1.0)
        self.web.urlChanged.connect(self._on_url_changed)
        self.web.titleChanged.connect(self._on_title_changed)
        self.web.loadFinished.connect(self._on_load_finished)
        self.back.clicked.connect(self.web.back)
        self.forward.clicked.connect(self.web.forward)
        self.reload.clicked.connect(self.web.reload)
        self.home.clicked.connect(self.go_current_comic_home)
        self.clean.toggled.connect(self.set_clean_view)
        self.save_item.clicked.connect(self._request_save_toggle)
        self.mark_read.clicked.connect(self._request_read_toggle)
        self.fit.currentIndexChanged.connect(self._fit_combo_changed)
        self.reader_mode_btn.clicked.connect(lambda: self.set_reading_mode(True, preserve_view=True))
        self.zoom_out.clicked.connect(lambda: self.adjust_zoom(-0.08))
        self.zoom_reset.clicked.connect(lambda: self.reset_zoom())
        self.zoom_in.clicked.connect(lambda: self.adjust_zoom(0.08))
        self.external.clicked.connect(lambda: QDesktopServices.openUrl(self.web.url()))
        root.addWidget(self.web, 1)

        # Compact floating navigation kept available in distraction-free reader mode.
        self.reader_nav = QFrame(self.reader_control_strip)
        self.reader_nav.setObjectName("readerNav")
        # Prevent mouse events on the floating controls from propagating to
        # the QWebEngineView underneath (BatCave treats clicks as page turns).
        self.reader_nav.setAttribute(Qt.WA_NoMousePropagation, True)
        nav = QHBoxLayout(self.reader_nav)
        nav.setContentsMargins(5, 5, 5, 5)
        nav.setSpacing(4)

        self.reader_back = QPushButton("←")
        self.reader_back.setObjectName("readerNavButton")
        self.reader_back.setFixedSize(40, 36)
        self.reader_back.setToolTip("Back")
        self.reader_back.clicked.connect(self.web.back)

        self.reader_home = QPushButton("⌂")
        self.reader_home.setObjectName("readerNavButton")
        self.reader_home.setFixedSize(40, 36)
        self.reader_home.setToolTip("Current comic main page")
        self.reader_home.clicked.connect(self.go_current_comic_home)

        self.reader_list = QPushButton("★")
        self.reader_list.setObjectName("readerNavButton")
        self.reader_list.setFixedSize(40, 36)
        self.reader_list.setToolTip("Reading List")
        self.reader_list.clicked.connect(self.readingListRequested.emit)

        self.reader_bookmark = QPushButton("🔖")
        self.reader_bookmark.setObjectName("readerNavButton")
        self.reader_bookmark.setFixedSize(40, 36)
        self.reader_bookmark.setToolTip("Bookmark this page")
        self.reader_bookmark.clicked.connect(self._request_bookmark)

        # Live page counter / selector for BatCave issues.
        self.reader_page = QComboBox()
        self.reader_page.setObjectName("readerPageSelector")
        self.reader_page.setFixedHeight(36)
        self.reader_page.setMinimumWidth(96)
        self.reader_page.setToolTip("Current page — choose another page to jump there")
        self.reader_page.addItem("Page -- / --", 0)
        self.reader_page.currentIndexChanged.connect(self._reader_page_selected)

        # Quick zoom controls for distraction-free reader mode.
        self.reader_zoom_out = QPushButton("−")
        self.reader_zoom_out.setObjectName("readerNavButton")
        self.reader_zoom_out.setFixedSize(40, 36)
        self.reader_zoom_out.setToolTip("Zoom out")
        self.reader_zoom_out.clicked.connect(lambda: self.adjust_zoom(-0.08))

        self.reader_fit = QPushButton("Fit")
        self.reader_fit.setObjectName("readerNavButton")
        self.reader_fit.setFixedSize(48, 36)
        self.reader_fit.setToolTip("Fit page / reset zoom")
        self.reader_fit.clicked.connect(self._reader_fit_page)

        self.reader_zoom_in = QPushButton("+")
        self.reader_zoom_in.setObjectName("readerNavButton")
        self.reader_zoom_in.setFixedSize(40, 36)
        self.reader_zoom_in.setToolTip("Zoom in")
        self.reader_zoom_in.clicked.connect(lambda: self.adjust_zoom(0.08))

        self.reader_exit = QPushButton("☰")
        self.reader_exit.setObjectName("readerNavButton")
        self.reader_exit.setFixedSize(40, 36)
        self.reader_exit.setToolTip("Show full controls (Esc)")
        self.reader_exit.clicked.connect(lambda: self.set_reading_mode(False))

        # Isolate mouse input for the floating toolbar. A press on a Qt overlay
        # button must never leak its release/click into BatCave's
        # web view, where it would be interpreted as "turn page".
        for ctl in (self.reader_back, self.reader_home, self.reader_list, self.reader_bookmark,
                    self.reader_zoom_out, self.reader_fit, self.reader_zoom_in,
                    self.reader_exit):
            ctl.setAttribute(Qt.WA_NoMousePropagation, True)
            ctl.pressed.connect(self._reader_toolbar_mouse_down)
            ctl.released.connect(self._reader_toolbar_mouse_up)

        for b in (self.reader_back, self.reader_home, self.reader_list, self.reader_bookmark):
            nav.addWidget(b)
        nav.addWidget(self.reader_page)
        nav.addWidget(self.reader_zoom_out)
        nav.addWidget(self.reader_fit)
        nav.addWidget(self.reader_zoom_in)
        nav.addWidget(self.reader_exit)

        self.reader_nav.adjustSize()
        self.reader_nav.hide()
        self.reader_nav.raise_()

        # The persistent reveal handle lives in the native control strip, never
        # over the webview, so it needs no mouse shielding.
        self.reader_handle = QPushButton("☰", self.reader_control_strip)
        self.reader_handle.setObjectName("readerHandle")
        self.reader_handle.setFixedSize(34, 30)
        self.reader_handle.setToolTip("Show reader controls")
        self.reader_handle.clicked.connect(self._reader_handle_clicked)
        self.reader_handle.hide()
        self.reader_handle.raise_()

        # The floating reader toolbar must not permanently cover the top of
        # full-bleed comic pages. It auto-hides after brief inactivity
        # and returns when the pointer touches the top edge. This changes only
        # the Qt overlay; BatCave's reader DOM and comic image stay untouched.
        self._reader_nav_last_activity = time.monotonic()
        self._reader_nav_autohide = QTimer(self)
        self._reader_nav_autohide.setInterval(150)
        self._reader_nav_autohide.timeout.connect(self._update_reader_nav_autohide)
        self._reader_nav_autohide.start()

        self.setMouseTracking(True)
        self.web.setMouseTracking(True)
        self.web.installEventFilter(self)

        # Poll BatCave's reader page indicator while an issue is open.
        # When the last page is reached, the issue is automatically marked Read.
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(1200)
        self._progress_timer.timeout.connect(self._check_issue_completion)
        self._progress_timer.start()
        self._last_auto_read_key = ""
        self._active_issue_url = ""
        self._active_issue_title = ""
        self._active_issue_current = 0
        self._active_issue_total = 0

    def _install_early_site_patches(self):
        """Install BatCave-only DOM guards before the page's normal scripts run.

        This prevents the first-visit FAQ modal from flashing for a frame before
        the later loadFinished cleanup can remove it.  The observer is narrowly
        targeted to the exact welcome-card text and does not alter the reader.
        """
        source = r"""
(() => {
  if (!/^(?:https?:\/\/)?(?:www\.)?batcave\.biz\//i.test(location.href || '')) return;
  if (window.__crEarlyWelcomeGuard) return;
  window.__crEarlyWelcomeGuard = true;

  const MARK='data-cr-welcome-popup';
  const isWelcomeText = txt => {
    txt=(txt||'').replace(/\s+/g,' ').trim();
    return txt.includes('Hello, newcomer or old-timer?') &&
           txt.includes('Open FAQ') &&
           txt.includes('Suggestions') &&
           txt.includes('Telegram');
  };

  const hideCard = card => {
    if(!card || card===document.body || card===document.documentElement) return false;
    let target=card;
    for(let p=card; p && p!==document.body && p!==document.documentElement; p=p.parentElement){
      try{
        const cs=getComputedStyle(p);
        const r=p.getBoundingClientRect();
        if(cs.position==='fixed' && r.width>=280 && r.height>=160) target=p;
      }catch(e){}
    }
    try{
      target.setAttribute(MARK,'1');
      target.style.setProperty('display','none','important');
      target.style.setProperty('visibility','hidden','important');
      target.style.setProperty('pointer-events','none','important');

      // BatCave creates the dim backdrop as a separate full-screen fixed node.
      // Hide it in the SAME MutationObserver turn so Chromium never paints a
      // dark frame after the welcome card itself has been suppressed.
      try{
        const vw=Math.max(document.documentElement.clientWidth||0, window.innerWidth||0);
        const vh=Math.max(document.documentElement.clientHeight||0, window.innerHeight||0);
        for(const el of document.querySelectorAll('body *')){
          if(el===target || target.contains(el) || el.contains(target)) continue;
          const cs=getComputedStyle(el);
          if(cs.position!=='fixed') continue;
          const r=el.getBoundingClientRect();
          if(r.width < vw*.88 || r.height < vh*.88) continue;
          const bg=cs.backgroundColor||'';
          const opacity=parseFloat(cs.opacity||'1');
          const z=parseInt(cs.zIndex||'0',10)||0;
          const looksDim=/rgba?\(/i.test(bg) && bg!=='rgba(0, 0, 0, 0)' && bg!=='transparent';
          if((looksDim || opacity<0.98) && z>=1){
            el.setAttribute('data-cr-welcome-backdrop','1');
            el.style.setProperty('display','none','important');
            el.style.setProperty('visibility','hidden','important');
            el.style.setProperty('pointer-events','none','important');
          }
        }
      }catch(e){}
      return true;
    }catch(e){ return false; }
  };

  const scanNode = node => {
    if(!node || node.nodeType!==1) return false;
    try{
      if(isWelcomeText(node.innerText || node.textContent || '')){
        // Find the smallest descendant/ancestor carrying the complete card text.
        let best=node, bestArea=Infinity;
        const candidates=[node, ...node.querySelectorAll('*')];
        for(const el of candidates){
          const txt=el.innerText || el.textContent || '';
          if(!isWelcomeText(txt)) continue;
          const r=el.getBoundingClientRect();
          const area=Math.max(1,r.width*r.height);
          if(area<bestArea){ best=el; bestArea=area; }
        }
        return hideCard(best);
      }
    }catch(e){}
    return false;
  };

  // MutationObserver callbacks run before the next paint, so a modal inserted
  // by BatCave's startup JS is hidden before it can visibly flash.
  const observer=new MutationObserver(mutations=>{
    for(const m of mutations){
      for(const n of m.addedNodes){
        if(scanNode(n)) return;
      }
    }
  });
  try{ observer.observe(document,{subtree:true,childList:true}); }catch(e){}
  window.__crEarlyWelcomeObserver=observer;

  // Catch markup that was already parsed before this script was scheduled.
  const rescan=()=>{
    try{
      const root=document.body||document.documentElement;
      if(root) scanNode(root);
    }catch(e){}
  };
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',rescan,{once:true,capture:true});
  } else {
    rescan();
  }
})();
"""
        script = QWebEngineScript()
        script.setName("ComicReader early BatCave popup blocker")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(source)
        self.web.page().scripts().insert(script)
        self._early_site_patch_script = script

    def _on_load_finished(self, ok):
        if ok:
            # Site-level safety/annoyance patches are independent of Clean View.
            # In particular, keep BatCave's welcome/FAQ popup blocked even when
            # the user intentionally wants the normal site layout visible.
            QTimer.singleShot(70, self.apply_site_patches)
            if self.clean_view:
                QTimer.singleShot(120, self.apply_clean_view)
            QTimer.singleShot(220, self.apply_tracking_marks)
            # Recently Read may be resuming from a legacy series URL. If so,
            # enter the issue reader first; once the reader appears the pending
            # page is applied by _on_title_changed/_apply_pending_resume_page.
            if self._pending_resume_page > 0 and '/reader/' not in self.web.url().path().lower():
                QTimer.singleShot(180, self._try_start_pending_issue)

    def set_tracking_data(self, saved_urls, read_urls):
        self.saved_urls = {canonical_url(u) for u in (saved_urls or []) if is_allowed_batcave_url(u)}
        self.read_urls = {canonical_url(u) for u in (read_urls or []) if is_allowed_batcave_url(u)}
        self._refresh_tracking_buttons()
        QTimer.singleShot(40, self.apply_tracking_marks)

    def _current_key(self):
        return canonical_url(self.web.url().toString())

    def _request_save_toggle(self):
        url = self.web.url().toString()
        if is_allowed_batcave_url(url):
            self.saveToggleRequested.emit(url, self._current_title or self.web.title())

    def _request_read_toggle(self):
        url = self.web.url().toString()
        if self._looks_like_issue(self._current_title) and is_allowed_batcave_url(url):
            self.readToggleRequested.emit(url, self._current_title or self.web.title())

    def _request_bookmark(self):
        # Read BatCave's native page selector at click time instead of relying
        # on the slower 1.2-second progress poll. This makes the bookmark button
        # work immediately after an issue opens or immediately after a page turn.
        url = self.web.url().toString()
        title = self._current_title or self.web.title()
        if not is_allowed_batcave_url(url):
            return

        script = r"""
(() => {
  const out={current:0,total:0};
  let sel=document.querySelector('.reader-root select');
  if(!sel){
    for(const candidate of document.querySelectorAll('select')){
      const opts=[...candidate.options];
      if(opts.length<2||opts.length>500) continue;
      const good=opts.filter(o=>/^\s*Page\s+\d+\s*\/\s*\d+\s*$/i.test((o.textContent||'').trim())).length;
      if(good>=Math.max(2,Math.floor(opts.length*.8))){sel=candidate;break;}
    }
  }
  if(sel){
    const opts=[...sel.options];
    out.total=opts.length;
    out.current=Math.max(1,sel.selectedIndex+1);
    const selected=opts[sel.selectedIndex];
    const m=(selected?.textContent||'').match(/Page\s+(\d+)\s*\/\s*(\d+)/i);
    if(m){
      const n=parseInt(m[1],10), t=parseInt(m[2],10);
      if(n>0) out.current=n;
      if(t>1) out.total=t;
    }
  }
  if(out.current<1){
    const hm=(location.hash||'').match(/page-(\d+)/i);
    if(hm) out.current=parseInt(hm[1],10)||0;
  }
  return JSON.stringify(out);
})();
"""

        def finish_bookmark(result, u=url, t=title):
            data = result
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = None
            current = 0
            total = 0
            if isinstance(data, dict):
                try:
                    current = int(data.get("current", 0) or 0)
                    total = int(data.get("total", 0) or 0)
                except Exception:
                    current = total = 0

            # Safe fallbacks if BatCave's DOM is briefly between page states.
            if current < 1:
                current = int(self._active_issue_current or 0)
            if total < 2:
                total = int(self.reader_page.property("pageTotal") or self._active_issue_total or 0)

            if current > 0 and total > 1:
                self.bookmarkRequested.emit(u, t, current, total)
                # Small visible acknowledgement so a successful click never
                # feels like a dead button. Restore the icon shortly after.
                self.reader_bookmark.setText("✓")
                QTimer.singleShot(700, lambda: self.reader_bookmark.setText("🔖"))
            else:
                self.reader_bookmark.setToolTip("Page still loading — try again in a moment")
                QTimer.singleShot(1500, lambda: self.reader_bookmark.setToolTip("Bookmark this page"))

        self.web.page().runJavaScript(script, finish_bookmark)

    def _refresh_tracking_buttons(self):
        key = self._current_key()
        saved = key in self.saved_urls
        read = key in self.read_urls
        self.save_item.setText("★ Saved" if saved else "☆ Save")
        is_issue = self._looks_like_issue(self._current_title)
        self.mark_read.setEnabled(is_issue)
        self.mark_read.setText("↶ Unread" if read else "✓ Read")

    def apply_tracking_marks(self):
        if not self.web.url().toString().startswith("http"):
            return
        saved = json.dumps(sorted(self.saved_urls))
        read = json.dumps(sorted(self.read_urls))
        script = r'''
(() => {
  const canon = u => { try { const x=new URL(u, location.href); x.hash=''; return x.href.replace(/\/$/,''); } catch(e) { return String(u||'').replace(/\/$/,''); } };
  const saved = new Set(__SAVED__);
  const read = new Set(__READ__);
  let st=document.getElementById('comic-reader-tracking-style');
  if(!st){ st=document.createElement('style'); st.id='comic-reader-tracking-style'; document.documentElement.appendChild(st); }
  st.textContent=`
    .cr-track-badge{display:inline-block!important;margin-left:7px!important;padding:2px 6px!important;border-radius:6px!important;font:600 11px/1.35 system-ui,sans-serif!important;vertical-align:middle!important;white-space:nowrap!important}
    .cr-read{background:#193b2a!important;color:#7de2aa!important;border:1px solid #2d6a49!important}
    .cr-saved{background:#3a3218!important;color:#f1d36b!important;border:1px solid #6d5b27!important}
  `;
  document.querySelectorAll('.cr-track-badge').forEach(x=>x.remove());
  for(const a of document.querySelectorAll('a[href]')){
    const k=canon(a.href);
    if(read.has(k)){ const b=document.createElement('span'); b.className='cr-track-badge cr-read'; b.textContent='✓ Read'; a.appendChild(b); }
    else if(saved.has(k)){ const b=document.createElement('span'); b.className='cr-track-badge cr-saved'; b.textContent='★ Saved'; a.appendChild(b); }
  }
})();
'''.replace('__SAVED__', saved).replace('__READ__', read)
        self.web.page().runJavaScript(script)

    def apply_site_patches(self):
        """Apply targeted BatCave fixes that should work in every view mode."""
        url = self.web.url().toString()
        if not is_allowed_batcave_url(url):
            return
        script = r"""
(() => {
  const STYLE_ID='comic-reader-site-patches';
  let st=document.getElementById(STYLE_ID);
  if(!st){
    st=document.createElement('style');
    st.id=STYLE_ID;
    (document.head||document.documentElement).appendChild(st);
  }
  st.textContent=`
    [data-cr-welcome-popup="1"],
    [data-cr-welcome-backdrop="1"] {
      display:none !important;
      visibility:hidden !important;
      pointer-events:none !important;
    }
  `;

  const hideWelcomePopup=()=>{
    if(!document.body) return false;
    let card=null;
    let cardArea=Infinity;
    for(const el of document.querySelectorAll('body *')){
      const txt=(el.innerText||'').replace(/\s+/g,' ').trim();
      if(!txt) continue;
      if(txt.includes('Hello, newcomer or old-timer?') &&
         txt.includes('Open FAQ') &&
         txt.includes('Suggestions') &&
         txt.includes('Telegram')){
        const r=el.getBoundingClientRect();
        const area=Math.max(1,r.width*r.height);
        if(area<cardArea){ card=el; cardArea=area; }
      }
    }
    if(!card) return false;

    // Hide the fixed modal/overlay ancestor when possible so the dim backdrop
    // disappears too. Never hide BODY/HTML or a full page content container.
    let target=card;
    for(let p=card; p && p!==document.body && p!==document.documentElement; p=p.parentElement){
      const cs=getComputedStyle(p);
      const r=p.getBoundingClientRect();
      if(cs.position==='fixed' && r.width>=280 && r.height>=160){
        target=p;
      }
    }
    target.dataset.crWelcomePopup='1';
    target.style.setProperty('display','none','important');
    target.style.setProperty('visibility','hidden','important');
    target.style.setProperty('pointer-events','none','important');

    // Remove a separate viewport-sized dim backdrop, if BatCave created one.
    // Keep the test deliberately strict so normal fixed navigation is untouched.
    try{
      const vw=Math.max(document.documentElement.clientWidth||0, window.innerWidth||0);
      const vh=Math.max(document.documentElement.clientHeight||0, window.innerHeight||0);
      for(const el of document.querySelectorAll('body *')){
        if(el===target || target.contains(el) || el.contains(target)) continue;
        const cs=getComputedStyle(el);
        if(cs.position!=='fixed') continue;
        const r=el.getBoundingClientRect();
        if(r.width < vw*.88 || r.height < vh*.88) continue;
        const bg=cs.backgroundColor||'';
        const opacity=parseFloat(cs.opacity||'1');
        const z=parseInt(cs.zIndex||'0',10)||0;
        const looksDim=/rgba?\(/i.test(bg) && bg!=='rgba(0, 0, 0, 0)' && bg!=='transparent';
        if((looksDim || opacity<0.98) && z>=1){
          el.dataset.crWelcomeBackdrop='1';
          el.style.setProperty('display','none','important');
          el.style.setProperty('visibility','hidden','important');
          el.style.setProperty('pointer-events','none','important');
        }
      }
    }catch(e){}
    return true;
  };

  try{ hideWelcomePopup(); }catch(e){}

  // Install only one observer per page. BatCave can inject the modal after
  // navigation or after its own startup scripts finish.
  if(!window.__crWelcomePopupObserver){
    window.__crWelcomePopupObserver=new MutationObserver(()=>{
      try{ hideWelcomePopup(); }catch(e){}
    });
    const root=document.body||document.documentElement;
    if(root) window.__crWelcomePopupObserver.observe(root,{subtree:true,childList:true});
  }

  // Retry for a few seconds because the first-visit popup may be injected late.
  if(window.__crWelcomePopupTimer) clearInterval(window.__crWelcomePopupTimer);
  let tries=0;
  window.__crWelcomePopupTimer=setInterval(()=>{
    tries++;
    try{ hideWelcomePopup(); }catch(e){}
    if(tries>=24){
      clearInterval(window.__crWelcomePopupTimer);
      window.__crWelcomePopupTimer=null;
    }
  },250);
})();
"""
        self.web.page().runJavaScript(script)

    def set_clean_view(self, on):
        self.clean_view = bool(on)
        self.clean.setText("Clean")
        # Welcome-popup blocking is intentionally independent of Clean View.
        self.apply_site_patches()
        if on:
            self.apply_clean_view()
        else:
            self.web.reload()

    def _fit_combo_changed(self, idx):
        mode = ["page", "width", "original"][idx]
        self.set_fit_mode(mode)

    def set_fit_mode(self, mode, emit=True):
        if mode not in ("page", "width", "original"):
            mode = "page"
        self.fit_mode = mode
        wanted = {"page": 0, "width": 1, "original": 2}[mode]
        if self.fit.currentIndex() != wanted:
            self.fit.blockSignals(True)
            self.fit.setCurrentIndex(wanted)
            self.fit.blockSignals(False)
        if emit:
            self.fitModeChanged.emit(mode)
        if self.clean_view:
            self.apply_clean_view()

    def adjust_zoom(self, delta, emit=True):
        # Comic-only zoom uses one stable baseline width per image and applies
        # an absolute factor to it. This survives BatCave replacing or
        # lazy-loading the page after a turn and
        # avoids compounding errors from repeatedly scaling the current size.
        old_factor = self.zoom_factor
        new_factor = max(0.50, min(2.50, round(old_factor + delta, 2)))
        if new_factor == old_factor:
            return
        self.zoom_factor = new_factor
        self.web.setZoomFactor(1.0)
        self.zoom_reset.setText(f"{int(self.zoom_factor * 100)}%")

        script = r"""
(() => {
  const factor = __FACTOR__;
  window.__crComicZoomFactor = factor;

  const isComicImage = img => {
    if (!img || img.tagName !== 'IMG') return false;
    const r = img.getBoundingClientRect();
    const src=(img.currentSrc||img.src||img.getAttribute('data-src')||'').toLowerCase();
    if (/avatar|comment|logo|icon|emoji|banner|advert|promo|badge|profile/.test(src)) return false;
    const naturalLarge=(img.naturalWidth||0) >= 450 && (img.naturalHeight||0) >= 550;
    const renderedLarge=r.width >= 400 && r.height >= 500;
    return naturalLarge || renderedLarge;
  };

  const applyOne = img => {
    if (!isComicImage(img)) return false;
    const r=img.getBoundingClientRect();
    if (!img.dataset.crZoomBaseWidth) {
      // Only capture a usable on-screen baseline.  If the image is currently
      // hidden during BatCave's transition, its load event/observer will retry.
      if (r.width < 100) return false;
      img.dataset.crZoomBaseWidth=String(r.width);
    }
    const base=parseFloat(img.dataset.crZoomBaseWidth||'0');
    if (!(base>0)) return false;
    img.dataset.crZoomTouched='1';
    img.style.setProperty('width', Math.max(1, base*factor)+'px','important');
    img.style.setProperty('height','auto','important');
    img.style.setProperty('max-width','none','important');
    img.style.setProperty('max-height','none','important');
    img.style.setProperty('object-fit','contain','important');
    img.style.setProperty('box-sizing','border-box','important');
    img.style.setProperty('display','block','important');
    img.style.setProperty('margin-left','auto','important');
    img.style.setProperty('margin-right','auto','important');
    return true;
  };

  const applyAll = () => {
    let any=false;
    for (const img of [...document.images]) any=applyOne(img)||any;
    document.documentElement.style.setProperty('overflow-x','auto','important');
    document.body.style.setProperty('overflow-x','auto','important');
    return any;
  };

  window.__crApplyComicZoom=applyAll;
  applyAll();

  // Install once. BatCave swaps/lazy-loads comic IMG nodes after page turns,
  // so automatically apply the user's current zoom to each new loaded page.
  if (!window.__crComicZoomObserver) {
    window.__crComicZoomObserver=new MutationObserver(muts=>{
      let needs=false;
      for(const m of muts){
        if(m.type==='childList' && m.addedNodes.length){ needs=true; break; }
        if(m.type==='attributes'){ needs=true; break; }
      }
      if(needs) requestAnimationFrame(()=>window.__crApplyComicZoom?.());
    });
    window.__crComicZoomObserver.observe(document.documentElement,{
      subtree:true,childList:true,attributes:true,attributeFilter:['src','srcset','class','style']
    });
    document.addEventListener('load',ev=>{
      if(ev.target && ev.target.tagName==='IMG') requestAnimationFrame(()=>window.__crApplyComicZoom?.());
    },true);
  }
})();
""".replace('__FACTOR__', str(float(self.zoom_factor)))
        self.web.page().runJavaScript(script)
        if emit:
            self.zoomChanged.emit(self.web.url().toString(), self._current_title or self.web.title(), float(self.zoom_factor))

    def set_zoom_factor(self, factor, emit=False):
        """Apply an absolute comic-image zoom factor without changing web-page zoom."""
        try:
            target = max(0.50, min(2.50, round(float(factor), 2)))
        except Exception:
            target = 1.0
        if abs(target - 1.0) < 0.001:
            self.reset_zoom(emit=emit)
            return
        # A new BatCave issue replaces the document and therefore loses the JS
        # observer/styles even if Python still remembers the same numeric zoom.
        # Force a fresh application when target == current.
        if abs(target - self.zoom_factor) < 0.001:
            self.zoom_factor = 1.0
        self.adjust_zoom(target - self.zoom_factor, emit=emit)

    def reset_zoom(self, emit=True):
        self.zoom_factor = 1.0
        self.web.setZoomFactor(1.0)
        self.zoom_reset.setText("100%")
        script = r"""
(() => {
  window.__crComicZoomFactor=1.0;
  for (const img of [...document.images]) {
    if (img.dataset.crZoomTouched !== '1' && !img.dataset.crZoomBaseWidth) continue;
    for (const prop of ['width','height','max-width','max-height','object-fit','box-sizing','display','margin-left','margin-right']) {
      img.style.removeProperty(prop);
    }
    delete img.dataset.crZoomTouched;
    delete img.dataset.crZoomBaseWidth;
  }
  document.documentElement.style.removeProperty('overflow-x');
  document.body.style.removeProperty('overflow-x');
})();
"""
        self.web.page().runJavaScript(script)
        if self.clean_view:
            QTimer.singleShot(20, self.apply_clean_view)
        if emit:
            self.zoomChanged.emit(self.web.url().toString(), self._current_title or self.web.title(), 1.0)

    def apply_clean_view(self):
        # Keep BatCave's reader DOM and lazy-loading layout intact. Only hide
        # obvious site chrome here. Comic image sizing is changed ONLY when the
        # user explicitly presses +/- or Fit, which avoids blank/black pages
        # caused by repeatedly forcing dimensions while BatCave is lazy-loading.
        script = r"""
(() => {
  const styleId='comic-reader-clean-view';
  let st=document.getElementById(styleId);
  if(!st){st=document.createElement('style');st.id=styleId;document.documentElement.appendChild(st);}
  st.textContent=`
    html, body { background:#000 !important; }
    body { margin-top:0 !important; padding-top:0 !important; }
    [class*="chat" i], [id*="chat" i],
    [class*="cookie" i], [id*="cookie" i],
    [class*="advert" i], [id*="advert" i] { display:none !important; }
  `;

  const issue=/^read\s+/i.test(document.title||'') || /\/reader\//i.test(location.pathname||'');
  if(!issue) return;

  // v0.8.7: preload adjacent comic pages instead of cloning the live
  // Chromium bitmap. The old transition shield used fixed clones of very
  // large decoded images; on some GPUs/QtWebEngine builds that produced
  // blocky/corrupted frames. Preloading keeps BatCave's own DOM untouched.
  if(!window.__crAdjacentPreloaderInstalled){
    window.__crAdjacentPreloaderInstalled=true;
    window.__crPreloadCache=window.__crPreloadCache||new Map();

    const pageSelect=()=>{
      let sel=document.querySelector('.reader-root select');
      if(sel) return sel;
      for(const candidate of document.querySelectorAll('select')){
        const opts=[...candidate.options];
        if(opts.length<2||opts.length>500) continue;
        const good=opts.filter(o=>/^\s*Page\s+\d+\s*\/\s*\d+\s*$/i.test((o.textContent||'').trim())).length;
        if(good>=Math.max(2,Math.floor(opts.length*.8))) return candidate;
      }
      return null;
    };

    const pageImages=()=>[...document.querySelectorAll('.reader__item-wrap img.reader__item, .reader-view img.reader__item')];

    const srcFor=(img)=> img ? (img.currentSrc||img.src||img.getAttribute('data-src')||img.getAttribute('data-lazy-src')||'') : '';

    const preload=(src)=>{
      if(!src || window.__crPreloadCache.has(src)) return;
      const im=new Image();
      window.__crPreloadCache.set(src, im);
      while(window.__crPreloadCache.size>8){
        const oldest=window.__crPreloadCache.keys().next().value;
        const old=window.__crPreloadCache.get(oldest);
        try{ if(old) old.src=''; }catch(e){}
        window.__crPreloadCache.delete(oldest);
      }
      im.decoding='async';
      im.src=src;
      // Ask Chromium to fully decode when possible, without touching the live page.
      try{ im.decode?.().catch(()=>{}); }catch(e){}
    };

    const warmNeighbors=()=>{
      const sel=pageSelect();
      const imgs=pageImages();
      if(!sel || !imgs.length) return;
      const i=Math.max(0,sel.selectedIndex);
      for(const n of [i-2,i-1,i,i+1,i+2]){
        if(n<0 || n>=imgs.length) continue;
        preload(srcFor(imgs[n]));
      }
    };

    warmNeighbors();
    window.__crPreloadTimer=setInterval(warmNeighbors,300);
    document.addEventListener('load',ev=>{
      if(ev.target&&ev.target.matches?.('img.reader__item')) warmNeighbors();
    },true);
  }

  // Hide only small fixed/sticky site controls. Do NOT hide footer/comments,
  // page containers, reader wrappers, or anything that can affect lazy loading.
  for(const el of [...document.querySelectorAll('body *')]){
    const r=el.getBoundingClientRect();
    const cs=getComputedStyle(el);
    const txt=(el.innerText||'').trim().replace(/\s+/g,' ');
    if((cs.position==='fixed'||cs.position==='sticky') && r.height>0 && r.height<180){
      if(/login|popular now|repair|free steam games|chat|bookmark.*report.*sound|table of contents|settings/i.test(txt)){
        el.style.setProperty('display','none','important');
      }
    }
    // Keep BatCave's own page counter alive but invisible so its JS can update.
    if(r.width<360 && r.height>0 && r.height<150 && /^(?:pages?\s*)?\d+(?:\s*[-–—]\s*\d+)?\s*\/\s*\d+/i.test(txt)){
      el.dataset.crNativeCounter='1';
      window.__crNativeCounterEl=el;
      window.__crNativeCounterText=txt;
      el.style.setProperty('opacity','0','important');
      el.style.setProperty('pointer-events','none','important');
      el.style.setProperty('visibility','visible','important');
    }
  }

  document.documentElement.style.setProperty('background','#000','important');
  document.body.style.setProperty('background','#000','important');
  // Important: leave image width/height/display/max-size untouched here.
})();
"""
        self.web.page().runJavaScript(script)


    def _check_issue_completion(self):
        if not self.reading_mode:
            return
        url = self.web.url().toString()
        if not is_allowed_batcave_url(url):
            return

        # BatCave exposes live page state through a SELECT with one option per
        # page; its selectedIndex tracks the active page.
        script = r"""
(() => {
  const out={current:0,total:0,source:'none',finished:false};

  // Exact BatCave reader selector first.
  let sel=document.querySelector('.reader-root select');
  if (!sel) {
    // Conservative fallback: find a select whose options look like
    // "Page N / TOTAL".
    for (const candidate of document.querySelectorAll('select')) {
      const opts=[...candidate.options];
      if (opts.length<2 || opts.length>500) continue;
      const good=opts.filter(o=>/^\s*Page\s+\d+\s*\/\s*\d+\s*$/i.test((o.textContent||'').trim())).length;
      if (good>=Math.max(2,Math.floor(opts.length*.8))) { sel=candidate; break; }
    }
  }

  if (sel) {
    const opts=[...sel.options];
    const total=opts.length;
    let current=sel.selectedIndex+1;
    const selected=opts[sel.selectedIndex];
    const m=(selected?.textContent||'').match(/Page\s+(\d+)\s*\/\s*(\d+)/i);
    if (m) {
      const n=parseInt(m[1],10), t=parseInt(m[2],10);
      if (n>=1 && n<=total) current=n;
      if (t===total) out.total=t;
    }
    if (!out.total) out.total=total;
    out.current=current;
    out.source='batcave-select';
  }

  // URL hash is a reliable current-page fallback on BatCave (#page-5).
  if (out.current<1) {
    const hm=(location.hash||'').match(/page-(\d+)/i);
    if (hm) out.current=parseInt(hm[1],10)||0;
  }

  // Active wrapper is another fallback for current page.
  if (out.current<1) {
    const wraps=[...document.querySelectorAll('.reader__item-wrap')];
    const active=wraps.findIndex(w=>w.classList.contains('active'));
    if (active>=0) out.current=active+1;
    if (out.total<2 && wraps.length>1) out.total=wraps.length;
  }

  if (out.total>1 && out.current>=out.total) out.finished=true;
  // Qt WebEngine is more reliable returning JSON text than a JS object.
  return JSON.stringify(out);
})();
"""
        self.web.page().runJavaScript(
            script,
            lambda result, u=url, t=self._current_title: self._issue_progress_result(u, t, result)
        )

    def _issue_progress_result(self, url, title, result):
        # PySide/Qt WebEngine can return plain JS objects as None depending on
        # the Chromium/PySide build, so the tracker returns JSON text for
        # consistent deserialization.
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                return
        if not isinstance(result, dict):
            return
        try:
            current = int(result.get("current", 0) or 0)
            total = int(result.get("total", 0) or 0)
            finished = bool(result.get("finished", False))
        except Exception:
            return
        if total > 1 and current > 0:
            self._update_reader_page_selector(current, total)
            live_title = title or self.web.title()
            self._active_issue_url = url
            self._active_issue_title = live_title
            self._active_issue_current = current
            self._active_issue_total = total
            # Persist per-issue progress in the main window.  This is emitted
            # on every valid poll, not only on completion.
            self.issueProgress.emit(url, live_title, current, total)
        if finished or (total > 1 and current >= total):
            key = canonical_url(url)
            if key not in self.read_urls and key != self._last_auto_read_key:
                self._last_auto_read_key = key
                self.autoReadDetected.emit(url, title or self.web.title(), current, total)

    def _update_reader_page_selector(self, current, total):
        current = max(1, int(current or 1))
        total = max(1, int(total or 1))
        if total <= 1:
            return
        existing_total = self.reader_page.property("pageTotal") or 0
        self.reader_page.blockSignals(True)
        if int(existing_total) != total:
            self.reader_page.clear()
            for n in range(1, total + 1):
                self.reader_page.addItem(f"Page {n} / {total}", n)
            self.reader_page.setProperty("pageTotal", total)
        self.reader_page.setCurrentIndex(max(0, min(current - 1, self.reader_page.count() - 1)))
        self.reader_page.blockSignals(False)
        self.reader_page.setToolTip(f"Page {current} of {total} — select a page to jump")
        self.reader_nav.adjustSize()

    def _reader_page_selected(self, index):
        if index < 0 or not self.reading_mode:
            return
        page_num = self.reader_page.itemData(index)
        try:
            page_num = int(page_num)
        except Exception:
            return
        if page_num < 1:
            return

        script = r"""
(() => {
  const target=__PAGE__;
  let sel=document.querySelector('.reader-root select');
  if (!sel) {
    for (const candidate of document.querySelectorAll('select')) {
      const opts=[...candidate.options];
      if (opts.length<target || opts.length<2 || opts.length>500) continue;
      const good=opts.filter(o=>/^\s*Page\s+\d+\s*\/\s*\d+\s*$/i.test((o.textContent||'').trim())).length;
      if (good>=Math.max(2,Math.floor(opts.length*.8))) { sel=candidate; break; }
    }
  }
  if (!sel || target>sel.options.length) return false;

  // Cancel any stabilizer left from a very fast previous page jump.
  try { window.__crManualJumpStabilizer?.stop?.(); } catch(e) {}

  sel.selectedIndex=target-1;
  sel.value=sel.options[target-1].value;

  // Use BatCave's normal selector path exactly once. Do not monkey-patch
  // scrolling/focus APIs: previous Page-1-specific guards could interfere
  // with later pages. Instead, after BatCave activates the requested page,
  // keep that page aligned at the top briefly while the site finishes any
  // delayed focus/comment/footer work.
  sel.dispatchEvent(new Event('change',{bubbles:true}));

  const started=performance.now();
  const lifetime=2400;
  let stopped=false;
  let timer=0;
  let raf=0;

  const targetIsActive=()=>{
    const wraps=[...document.querySelectorAll('.reader__item-wrap')];
    if (!wraps.length || target>wraps.length) return null;
    const wanted=wraps[target-1];
    if (!wanted) return null;
    const active=document.querySelector('.reader__item-wrap.active');
    // Prefer the exact indexed wrapper once it becomes active. If BatCave
    // temporarily omits the active class, only trust it when the native
    // selector itself still reports the requested page.
    if (wanted.classList.contains('active')) return wanted;
    if (active && active===wanted) return wanted;
    if (sel.selectedIndex===target-1 && getComputedStyle(wanted).display!=='none') return wanted;
    return null;
  };

  const pin=()=>{
    if (stopped || !/\/reader\//i.test(location.pathname||'')) return;
    const wrap=targetIsActive();
    if (!wrap) return;
    const r=wrap.getBoundingClientRect();
    if (!Number.isFinite(r.top)) return;
    const se=document.scrollingElement || document.documentElement;
    if (!se) return;
    // Recompute on every pass so image decoding/layout shifts above the page
    // do not accumulate error. Avoid smooth scrolling to prevent oscillation.
    const desired=Math.max(0, se.scrollTop + r.top);
    if (Math.abs(se.scrollTop-desired)>1) se.scrollTop=desired;
  };

  const tick=()=>{
    if (stopped) return;
    if (performance.now()-started>=lifetime) { stop(); return; }
    pin();
    raf=requestAnimationFrame(tick);
  };

  const stop=()=>{
    if (stopped) return;
    stopped=true;
    if (timer) clearInterval(timer);
    if (raf) cancelAnimationFrame(raf);
    try { delete window.__crManualJumpStabilizer; } catch(e) {}
  };

  window.__crManualJumpStabilizer={stop};
  // A short interval catches delayed site timers even in throttled frames;
  // requestAnimationFrame keeps the visible position stable between them.
  timer=setInterval(pin,90);
  raf=requestAnimationFrame(tick);
  setTimeout(stop,lifetime+80);
  return true;
})();
""".replace("__PAGE__", str(page_num))
        self.web.page().runJavaScript(script)

        # Persist jumps from our own selector immediately. If the user jumps
        # straight to the final page and opens the next issue before the timer
        # polls again, the completed issue is still recorded correctly.
        try:
            total = int(self.reader_page.property("pageTotal") or 0)
        except Exception:
            total = 0
        if total > 1:
            url = self.web.url().toString()
            title = self._current_title or self.web.title()
            self._active_issue_url = url
            self._active_issue_title = title
            self._active_issue_current = page_num
            self._active_issue_total = total
            self.issueProgress.emit(url, title, page_num, total)
            if page_num >= total:
                key = canonical_url(url)
                if key not in self.read_urls and key != self._last_auto_read_key:
                    self._last_auto_read_key = key
                    self.autoReadDetected.emit(url, title, page_num, total)


    def go_current_comic_home(self):
        """Open the main series/comic page for the issue currently being read.

        BatCave reader URLs use /reader/... while the series page is a normal
        *.html comic page.  We resolve the breadcrumb/link from the loaded DOM
        instead of guessing the numeric comic id from the reader URL.
        """
        script = r"""
(() => {
  try {
    const here = location.href;
    // If we are already on a normal comic page, keep it there.
    if (!/\/reader\//i.test(location.pathname)) return here;

    const pageTitle = (document.title || '').replace(/^Read\s+/i, '')
      .replace(/\s+comics online.*$/i, '')
      .trim();
    const baseTitle = pageTitle.replace(/\s+#\s*\d+.*$/i, '').trim().toLowerCase();

    const links = Array.from(document.querySelectorAll('a[href]')).map((a, i) => {
      let href = '';
      try { href = new URL(a.getAttribute('href'), location.href).href; } catch(e) {}
      const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
      return {href, text, i};
    }).filter(x => x.href && /(^|\.)batcave\.biz$/i.test(new URL(x.href).hostname));

    let best = null, bestScore = -1;
    for (const x of links) {
      let u; try { u = new URL(x.href); } catch(e) { continue; }
      const path = u.pathname;
      if (/\/reader\//i.test(path)) continue;
      // BatCave comic/series pages look like /33051-absolute-batman-2024.html
      const isComicPage = /^\/\d+-[^/]+\.html$/i.test(path);
      if (!isComicPage) continue;
      const t = x.text.toLowerCase();
      let score = 10;
      if (baseTitle && t === baseTitle) score += 100;
      else if (baseTitle && (t.includes(baseTitle) || baseTitle.includes(t))) score += 60;
      // Breadcrumb links are normally near the top of the DOM.
      score += Math.max(0, 20 - Math.min(20, x.i / 10));
      if (score > bestScore) { bestScore = score; best = x.href; }
    }
    return best || '';
  } catch (e) { return ''; }
})();
"""

        def open_result(result):
            if isinstance(result, str) and is_allowed_batcave_url(result):
                self.web.setUrl(QUrl(result))
                return
            # Safer fallback than BatCave home: go back in history, which for a
            # normally-opened issue is the comic/series page the user came from.
            if self.web.history().canGoBack():
                self.web.back()
            else:
                self.web.setUrl(QUrl(BATCAVE_URL))

        self.web.page().runJavaScript(script, open_result)

    def _reader_handle_clicked(self):
        # BatCave can change its own scroll position when the native reader
        # controls regain focus/visibility. Snapshot the exact
        # page position before revealing the toolbar and restore it briefly
        # afterward. This does not touch the comic IMG or reader DOM.
        if not self.reading_mode:
            return

        script = r"""
(() => JSON.stringify({
  href: location.href,
  x: window.scrollX || 0,
  y: window.scrollY || 0,
  hash: location.hash || ''
}))();
"""
        self.web.page().runJavaScript(script, self._reader_handle_position_ready)

    def _reader_handle_position_ready(self, result):
        if not self.reading_mode:
            return
        state = {}
        if isinstance(result, str):
            try:
                state = json.loads(result)
            except Exception:
                state = {}

        self._reader_nav_last_activity = time.monotonic()
        self.reader_handle.hide()
        self.reader_nav.show()
        self.reader_nav.raise_()

        try:
            x = float(state.get("x", 0) or 0)
            y = float(state.get("y", 0) or 0)
        except Exception:
            x, y = 0.0, 0.0
        href = str(state.get("href", "") or "")
        if not href:
            return

        # Restore several times because BatCave may perform a delayed focus or
        # layout scroll after the native control click has already completed.
        restore = r"""
(() => {
  if (location.href !== __HREF__) return false;
  const x=__X__, y=__Y__;
  if (Math.abs((window.scrollY||0)-y) > 2 || Math.abs((window.scrollX||0)-x) > 2)
    window.scrollTo({left:x, top:y, behavior:'instant'});
  return true;
})();
""".replace('__HREF__', json.dumps(href)).replace('__X__', str(x)).replace('__Y__', str(y))
        for delay in (0, 35, 90, 180, 320):
            QTimer.singleShot(delay, lambda s=restore: self.web.page().runJavaScript(s))

    def _reader_toolbar_mouse_down(self):
        # Temporarily make the web view ignore mouse input while a floating
        # toolbar control is held.  This blocks both the press and the release
        # from reaching BatCave's click-to-turn-page handler.
        self._reader_nav_last_activity = time.monotonic()
        self.web.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _reader_toolbar_mouse_up(self):
        # Keep the web view shielded briefly after release; on Windows/Chromium
        # the synthesized click can arrive a few milliseconds later.
        QTimer.singleShot(140, lambda: self.web.setAttribute(Qt.WA_TransparentForMouseEvents, False))

    def _reader_fit_page(self):
        # Return to the app's clean Fit Page view and 100% comic-image zoom.
        self.set_fit_mode("page")
        self.reset_zoom()
        if self.clean_view:
            QTimer.singleShot(30, self.apply_clean_view)

    def _show_reader_nav_temporarily(self):
        if not self.reading_mode:
            return
        self._reader_nav_last_activity = time.monotonic()
        if hasattr(self, "reader_handle"):
            self.reader_handle.hide()
        if not self.reader_nav.isVisible():
            self.reader_nav.show()
        self.reader_nav.raise_()

    def _update_reader_nav_autohide(self):
        if not self.reading_mode:
            return
        now = time.monotonic()

        # The controls now live in a dedicated Qt strip above the webview.
        # Hovering the strip/toolbar keeps it visible; otherwise it collapses
        # to the persistent handle after the usual delay.
        if self.reader_nav.isVisible():
            if self.reader_nav.underMouse():
                self._reader_nav_last_activity = now
                return
            if now - self._reader_nav_last_activity >= 2.4:
                self.reader_nav.hide()
                self.reader_handle.show()
                self.reader_handle.raise_()

    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Position controls inside the fixed native strip, not over the
        # comic/webview.
        self.reader_nav.adjustSize()
        self.reader_nav.move(8, 4)
        self.reader_nav.raise_()
        if hasattr(self, "reader_handle"):
            self.reader_handle.move(8, 9)
            self.reader_handle.raise_()

    def _looks_like_issue(self, title):
        t = (title or "").strip().lower()
        return t.startswith("read ") and " comics online" in t

    def _on_url_changed(self, url):
        u = url.toString()
        new_key = canonical_url(u)

        # If BatCave leaves an issue after we had already observed its final
        # page, commit it as Read before the new issue replaces the title/DOM.
        if self._active_issue_url:
            old_key = canonical_url(self._active_issue_url)
            if new_key != old_key and self._active_issue_total > 1 and self._active_issue_current >= self._active_issue_total:
                if old_key not in self.read_urls and old_key != self._last_auto_read_key:
                    self._last_auto_read_key = old_key
                    self.autoReadDetected.emit(
                        self._active_issue_url,
                        self._active_issue_title or self._current_title,
                        self._active_issue_current,
                        self._active_issue_total,
                    )

        if new_key != self._last_auto_read_key:
            self._last_auto_read_key = ""
        if is_allowed_batcave_url(u):
            self.urlSaved.emit(u)
        self._refresh_tracking_buttons()

    def _on_title_changed(self, title):
        self._current_title = title or ""
        self.status.setText(title[:100] if title else "BatCave")
        is_issue = self._looks_like_issue(title)
        if hasattr(self, "issue_tools"):
            self.issue_tools.setVisible(is_issue)
        if not is_issue:
            self.reader_page.blockSignals(True)
            self.reader_page.clear()
            self.reader_page.addItem("Page -- / --", 0)
            self.reader_page.setProperty("pageTotal", 0)
            self.reader_page.blockSignals(False)
        if is_issue:
            self.issueChanged.emit(self.web.url().toString(), title)
        self._refresh_tracking_buttons()
        self.set_reading_mode(is_issue)
        if is_issue and self._pending_resume_page > 0:
            self._pending_resume_attempts = 0
            QTimer.singleShot(180, self._apply_pending_resume_page)

    def set_reading_mode(self, on, preserve_view=False):
        on = bool(on)
        if self.reading_mode == on:
            # Make sure the floating navigation is restored if a page reload
            # left us in reader mode.
            if on:
                self.reader_control_strip.show()
                self._show_reader_nav_temporarily()
            return
        self.reading_mode = on
        self.controls.setVisible(not on)
        self.reader_control_strip.setVisible(on)
        self.reader_nav.setVisible(on)
        if hasattr(self, "reader_handle"):
            self.reader_handle.hide()
        if on:
            self.reader_nav.adjustSize()
            self.reader_nav.move(8, 4)
            self.reader_handle.move(8, 9)
            self._show_reader_nav_temporarily()
        self.readingModeChanged.emit(on)
        if on and self.clean_view and not preserve_view:
            # Automatic entry when an issue first opens still applies the clean
            # reader layout. Manual re-entry via the Reader Mode button must
            # preserve the exact current comic zoom/size and scroll position.
            QTimer.singleShot(80, self.apply_clean_view)
        self.web.setFocus()

    def _try_start_pending_issue(self):
        if self._pending_resume_page <= 0:
            return
        # If navigation already reached the reader, just apply the page.
        if '/reader/' in self.web.url().path().lower() or self._looks_like_issue(self._current_title):
            self._apply_pending_resume_page()
            return
        script = r"""
(() => {
  const norm = s => String(s||'').replace(/\s+/g,' ').trim().toLowerCase();
  const candidates=[...document.querySelectorAll('a[href],button,[role="button"]')];
  let best=null;
  for(const el of candidates){
    const txt=norm(el.innerText||el.textContent||el.getAttribute('aria-label')||'');
    if(txt==='start reading' || txt.startsWith('start reading ')){ best=el; break; }
  }
  if(!best) return false;
  try{ best.click(); return true; }catch(e){ return false; }
})();
"""
        self.web.page().runJavaScript(script, self._pending_start_result)

    def _pending_start_result(self, started):
        if self._pending_resume_page <= 0:
            return
        self._pending_resume_attempts += 1
        # Some BatCave buttons navigate asynchronously. Retry a few times if
        # the button wasn't present yet, but never loop indefinitely.
        if not started and self._pending_resume_attempts < 8:
            QTimer.singleShot(300, self._try_start_pending_issue)

    def _apply_pending_resume_page(self):
        page = int(self._pending_resume_page or 0)
        if page <= 0:
            return
        self._pending_resume_attempts += 1
        script = r"""
(() => {
  const target=__PAGE__;
  let sel=document.querySelector('.reader-root select');
  if(!sel){
    for(const candidate of document.querySelectorAll('select')){
      const opts=[...candidate.options];
      if(opts.length<target || opts.length<2 || opts.length>500) continue;
      const good=opts.filter(o=>/^\s*Page\s+\d+\s*\/\s*\d+\s*$/i.test((o.textContent||'').trim())).length;
      if(good>=Math.max(2,Math.floor(opts.length*.8))){ sel=candidate; break; }
    }
  }
  if(!sel || target>sel.options.length) return false;
  sel.selectedIndex=target-1;
  sel.value=sel.options[target-1].value;
  sel.dispatchEvent(new Event('input',{bubbles:true}));
  sel.dispatchEvent(new Event('change',{bubbles:true}));
  try{ history.replaceState(history.state,'',location.pathname+location.search+'#page-'+target); }catch(e){}
  return true;
})();
""".replace('__PAGE__', str(page))
        self.web.page().runJavaScript(script, self._pending_page_result)

    def _pending_page_result(self, applied):
        if applied:
            # Keep the real reader URL for future Recent resumes.
            self._pending_resume_page = 0
            self._pending_resume_source_url = ''
            self._pending_resume_attempts = 0
            QTimer.singleShot(100, self._check_issue_completion)
            return
        if self._pending_resume_page > 0 and self._pending_resume_attempts < 12:
            QTimer.singleShot(250, self._apply_pending_resume_page)

    def resume_issue(self, url, page=0):
        url = safe_batcave_url(url)
        try:
            page = max(0, int(page or 0))
        except Exception:
            page = 0
        self._pending_resume_page = page
        self._pending_resume_source_url = url
        self._pending_resume_attempts = 0
        q = QUrl(url)
        q.setFragment('')
        # A genuine reader URL can resume directly. For old/series URLs we load
        # the series page and automatically press Start Reading after it loads.
        if '/reader/' in q.path().lower() and page > 0:
            q.setFragment(f'page-{page}')
        self.web.setUrl(q)

    def resume(self, url):
        self._pending_resume_page = 0
        self._pending_resume_source_url = ''
        self._pending_resume_attempts = 0
        self.web.setUrl(QUrl(safe_batcave_url(url)))


def build_paneleo_icon(size=64):
    """Create a small two-panel Paneleo mark without bundling an asset file."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#111214"))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)
    gap = max(3, size // 16)
    pad = max(9, size // 7)
    panel_w = (size - pad * 2 - gap) // 2
    painter.setBrush(QColor("#ff5a36"))
    painter.drawRoundedRect(pad, pad, panel_w, size - pad * 2, 3, 3)
    painter.setBrush(QColor("#f4f0e8"))
    painter.drawRoundedRect(pad + panel_w + gap, pad, panel_w, size - pad * 2, 3, 3)
    painter.end()
    return QIcon(pix)


class NavButton(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setCheckable(True)
        self.setObjectName("navButton")


class MainWindow(QMainWindow):
    HOME, LIBRARY, READER, BATCAVE, READING_LIST, SERIES_DETAILS, BOOKMARKS = 0, 1, 2, 3, 4, 5, 6

    def __init__(self):
        super().__init__()
        self.settings = sanitize_settings(load_json(SETTINGS_FILE, {}))
        self.progress_store = sanitize_progress(load_json(PROGRESS_FILE, {}))
        self.batcave_library = sanitize_batcave_library(load_json(BATCAVE_LIBRARY_FILE, {"saved": {}, "read": {}}))
        self.batcave_library.setdefault("saved", {})
        self.batcave_library.setdefault("read", {})
        self.batcave_library.setdefault("issues", {})
        self.batcave_library.setdefault("bookmarks", {})
        self.comic_dir = self.settings.get("comic_dir", "")
        self.last_local_file = self.settings.get("last_local_file", "")
        self.last_local_page = int(self.settings.get("last_local_page", 0) or 0)
        self.last_local_total = int(self.settings.get("last_local_total", 0) or 0)
        self.last_local_opened = int(self.settings.get("last_local_opened", 0) or 0)
        self.last_batcave_url = safe_batcave_url(self.settings.get("last_batcave_url", BATCAVE_URL))
        raw_last_issue = self.settings.get("last_batcave_issue_url", "")
        self.last_batcave_issue_url = raw_last_issue if is_allowed_batcave_url(raw_last_issue) else ""
        self.last_batcave_title = self.settings.get("last_batcave_title", "")
        self.sidebar_collapsed = bool(self.settings.get("sidebar_collapsed", False))
        self.reading_list_expanded = self.settings.get("reading_list_expanded", {})
        if not isinstance(self.reading_list_expanded, dict):
            self.reading_list_expanded = {}
        self.series_zoom = self.settings.get("series_zoom", {})
        if not isinstance(self.series_zoom, dict):
            self.series_zoom = {}
        self.reading_sort_mode = self.settings.get("reading_sort_mode", "Recently read")
        if self.reading_sort_mode not in ("Recently read", "Title A–Z", "Progress"):
            self.reading_sort_mode = "Recently read"
        self.batcave_fit_mode = self.settings.get("batcave_fit_mode", "page")
        self.current_series_key = ""
        self.current_series_url = ""
        self.current_series_name = ""
        # Preserve the exact pre-fullscreen window/sidebar state so exiting
        # fullscreen never shrinks a previously maximized Paneleo window.
        self._fullscreen_restore_maximized = False
        self._fullscreen_restore_geometry = QByteArray()
        self._fullscreen_sidebar_visible = True
        self._fullscreen_mini_sidebar_visible = False
        self._fullscreen_reader_mode = False
        # Cover artwork is fetched only from BatCave over HTTPS. Full images
        # stay in memory; only bounded, reduced JPEG thumbnails are persisted.
        self.cover_network = QNetworkAccessManager(self)
        self._cover_pixmaps = {}
        self._cover_waiters = {}
        self._cover_page_pending = set()
        self._cover_image_pending = set()
        self._cover_failed = {}
        self._cover_page_cache = {}
        self._cover_live_urls = {}
        self._cover_cookie_pairs = {}
        self._cover_cookie_refresh_scheduled = False
        # Cover jobs must not run until Chromium has a real BatCave document
        # context. QWebEngineView.url() changes before the first navigation has
        # finished, which made startup cover jobs disappear on Windows.
        self._cover_context_ready = False
        self._cover_context_probe_pending = False
        self._prime_cover_metadata_from_library()
        # Cover jobs are executed inside the existing embedded BatCave page.
        # There is deliberately no separate/offscreen WebEngine view or page.
        self._cover_web_queue = []
        self._cover_web_active = None
        self._cover_web_page = None
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(build_paneleo_icon())
        self.setMinimumSize(1040, 680)
        self.resize(1320, 860)
        self._build_ui()
        # Cover fetches reuse the existing embedded BatCave page. No
        # second/offscreen WebEngine page is created, avoiding the transient
        # blank Paneleo window that could flash during background cover work.
        self._cover_web_page = None
        self.browser.web.loadFinished.connect(self._cover_browser_context_loaded)
        self._init_cover_session()
        # The BrowserWidget starts navigation inside its constructor, before
        # MainWindow can attach this loadFinished handler. Probe the actual JS
        # context as a race-free startup fallback.
        QTimer.singleShot(250, lambda: self._probe_cover_browser_context(0))
        self.apply_style()
        self.show_page(self.HOME)
        if self.comic_dir and Path(self.comic_dir).exists():
            self.scan_library()
        self.refresh_home()
        geometry_hex = self.settings.get("window_geometry", "")
        if isinstance(geometry_hex, str) and geometry_hex:
            try:
                self.restoreGeometry(QByteArray.fromHex(QByteArray(geometry_hex.encode("ascii"))))
            except Exception:
                pass

    def _build_ui(self):
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.setCentralWidget(shell)

        self.sidebar = QWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(236)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(20, 26, 16, 20)
        sl.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 2, 0)
        brand_mark = QLabel("▮▯")
        brand_mark.setObjectName("brandMark")
        brand = QLabel("PANELEO")
        brand.setObjectName("brand")
        brand_row.addWidget(brand_mark)
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        sl.addLayout(brand_row)
        brand_sub = QLabel("COMIC LIBRARY")
        brand_sub.setObjectName("brandSub")
        sl.addWidget(brand_sub)
        sl.addSpacing(26)

        nav_label = QLabel("LIBRARY")
        nav_label.setObjectName("navSection")
        sl.addWidget(nav_label)

        self.home_btn = NavButton("⌂   Home")
        self.lib_btn = NavButton("▦   Local library")
        self.web_btn = NavButton("◉   BatCave")
        self.saved_btn = NavButton("★   Reading list")
        self.bookmarks_btn = NavButton("◆   Bookmarks")

        self.home_btn.clicked.connect(lambda: self.show_page(self.HOME))
        self.lib_btn.clicked.connect(lambda: self.show_page(self.LIBRARY))
        self.web_btn.clicked.connect(self.open_batcave_home)
        self.saved_btn.clicked.connect(lambda: self.show_page(self.READING_LIST))
        self.bookmarks_btn.clicked.connect(lambda: self.show_page(self.BOOKMARKS))

        for btn in (self.home_btn, self.lib_btn, self.web_btn, self.saved_btn, self.bookmarks_btn):
            sl.addWidget(btn)

        sl.addSpacing(24)
        tools_label = QLabel("LOCAL FILES")
        tools_label.setObjectName("navSection")
        sl.addWidget(tools_label)

        self.open_btn = QPushButton("＋  Open comic")
        self.folder_btn = QPushButton("▣  Choose folder")
        self.refresh_btn = QPushButton("↻  Refresh")
        for btn in (self.open_btn, self.folder_btn, self.refresh_btn):
            btn.setObjectName("sideUtility")
        self.open_btn.clicked.connect(self.open_single_file)
        self.folder_btn.clicked.connect(self.choose_folder)
        self.refresh_btn.clicked.connect(self.scan_library)
        sl.addWidget(self.open_btn)
        sl.addWidget(self.folder_btn)
        sl.addWidget(self.refresh_btn)
        sl.addStretch(1)

        self.sidebar_toggle = QPushButton("«  Collapse")
        self.sidebar_toggle.setObjectName("sidebarToggle")
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        sl.addWidget(self.sidebar_toggle)
        ver = QLabel(f"Paneleo {APP_VERSION}")
        ver.setObjectName("versionLabel")
        sl.addWidget(ver)
        shell_layout.addWidget(self.sidebar)

        self.mini_sidebar = QWidget()
        self.mini_sidebar.setObjectName("MiniSidebar")
        self.mini_sidebar.setFixedWidth(54)
        msl = QVBoxLayout(self.mini_sidebar)
        msl.setContentsMargins(6, 14, 6, 14)
        self.expand_sidebar_btn = QPushButton("»")
        self.expand_sidebar_btn.setObjectName("miniExpand")
        self.expand_sidebar_btn.setToolTip("Show navigation")
        self.expand_sidebar_btn.clicked.connect(self.toggle_sidebar)
        msl.addWidget(self.expand_sidebar_btn)
        msl.addSpacing(16)
        for icon, tip, action in (
            ("⌂", "Home", lambda: self.show_page(self.HOME)),
            ("▦", "Local library", lambda: self.show_page(self.LIBRARY)),
            ("◉", "BatCave", self.open_batcave_home),
            ("★", "Reading list", lambda: self.show_page(self.READING_LIST)),
            ("◆", "Bookmarks", lambda: self.show_page(self.BOOKMARKS)),
        ):
            b = QPushButton(icon)
            b.setObjectName("miniNav")
            b.setToolTip(tip)
            b.clicked.connect(action)
            msl.addWidget(b)
        msl.addStretch(1)
        shell_layout.addWidget(self.mini_sidebar)
        self.mini_sidebar.hide()

        self.pages = QStackedWidget()
        shell_layout.addWidget(self.pages, 1)

        # HOME
        home = QWidget()
        home.setObjectName("homePage")
        hl = QVBoxLayout(home)
        hl.setContentsMargins(44, 34, 44, 34)
        hl.setSpacing(24)

        home_top = QHBoxLayout()
        home_title_stack = QVBoxLayout()
        home_title_stack.setSpacing(2)
        h1 = QLabel("Continue reading")
        h1.setObjectName("displayHeading")
        self.home_summary = QLabel("Pick up where you left off.")
        self.home_summary.setObjectName("muted")
        home_title_stack.addWidget(h1)
        home_title_stack.addWidget(self.home_summary)
        home_top.addLayout(home_title_stack)
        home_top.addStretch(1)
        self.home_fullscreen_btn = QPushButton("Fullscreen")
        self.home_fullscreen_btn.setObjectName("SecondaryButton")
        self.home_fullscreen_btn.setToolTip("Toggle fullscreen (F11)")
        self.home_fullscreen_btn.clicked.connect(self.toggle_window_fullscreen)
        home_browse = QPushButton("Browse BatCave")
        home_browse.setObjectName("SecondaryButton")
        home_browse.clicked.connect(self.open_batcave_home)
        home_top.addWidget(self.home_fullscreen_btn)
        home_top.addWidget(home_browse)
        hl.addLayout(home_top)

        hero = QFrame()
        hero.setObjectName("HeroCard")
        hero.setMinimumWidth(880)
        hero.setMaximumWidth(1160)
        hero_l = QHBoxLayout(hero)
        hero_l.setContentsMargins(0, 0, 0, 0)
        hero_l.setSpacing(0)
        self.home_hero_accent = QFrame()
        self.home_hero_accent.setObjectName("heroAccent")
        self.home_hero_accent.setFixedWidth(7)
        hero_l.addWidget(self.home_hero_accent)

        self.home_cover_art = QLabel()
        self.home_cover_art.setObjectName("CoverThumbnail")
        self.home_cover_art.setFixedSize(168, 252)
        self.home_cover_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_cover_art.setScaledContents(False)
        self.home_cover_art.show()
        hero_l.addWidget(self.home_cover_art)

        hero_info = QWidget()
        hero_info.setObjectName("HeroBody")
        hero_info_l = QVBoxLayout(hero_info)
        hero_info_l.setContentsMargins(30, 26, 30, 26)
        hero_info_l.setSpacing(9)
        self.home_continue_status = QLabel("READY TO READ")
        self.home_continue_status.setObjectName("heroStatus")
        self.home_continue_title = QLabel("Nothing in progress yet")
        self.home_continue_title.setTextFormat(Qt.TextFormat.PlainText)
        self.home_continue_title.setWordWrap(True)
        self.home_continue_title.setMinimumHeight(76)
        self.home_continue_title.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        self.home_continue_title.setObjectName("heroTitle")
        self.home_continue_meta = QLabel("Open BatCave or a local comic to get started.")
        self.home_continue_meta.setTextFormat(Qt.TextFormat.PlainText)
        self.home_continue_meta.setObjectName("heroMeta")
        self.home_continue_submeta = QLabel("Paneleo will remember the exact issue and page.")
        self.home_continue_submeta.setTextFormat(Qt.TextFormat.PlainText)
        self.home_continue_submeta.setWordWrap(True)
        self.home_continue_submeta.setObjectName("muted")
        self.home_continue_progress = QProgressBar()
        self.home_continue_progress.setObjectName("heroProgress")
        self.home_continue_progress.setTextVisible(False)
        self.home_continue_progress.setMaximum(100)
        self.home_continue_progress.setValue(0)
        hero_actions = QHBoxLayout()
        hero_actions.setSpacing(10)
        self.home_continue_btn = QPushButton("Continue reading")
        self.home_continue_btn.setObjectName("PrimaryButton")
        self.home_continue_btn.clicked.connect(self.resume_home_primary)
        self.home_batcave_btn = QPushButton("BatCave home")
        self.home_batcave_btn.setObjectName("TextButton")
        self.home_batcave_btn.clicked.connect(self.open_batcave_home)
        hero_actions.addWidget(self.home_continue_btn)
        hero_actions.addWidget(self.home_batcave_btn)
        hero_actions.addStretch(1)
        hero_info_l.addWidget(self.home_continue_status)
        hero_info_l.addWidget(self.home_continue_title)
        hero_info_l.addWidget(self.home_continue_meta)
        hero_info_l.addWidget(self.home_continue_submeta)
        hero_info_l.addSpacing(2)
        hero_info_l.addWidget(self.home_continue_progress)
        hero_info_l.addSpacing(2)
        hero_info_l.addLayout(hero_actions)
        hero_l.addWidget(hero_info, 1)
        hl.addWidget(hero, 0, Qt.AlignmentFlag.AlignLeft)

        summary_line = QFrame()
        summary_line.setObjectName("librarySummary")
        summary_layout = QHBoxLayout(summary_line)
        summary_layout.setContentsMargins(2, 0, 2, 0)
        summary_layout.setSpacing(26)
        self.home_stat_saved = QLabel("0")
        self.home_stat_read = QLabel("0")
        self.home_stat_bookmarks = QLabel("0")
        for number in (self.home_stat_saved, self.home_stat_read, self.home_stat_bookmarks):
            number.setObjectName("summaryNumber")
        self.home_stat_saved_label = QLabel("series")
        self.home_stat_read_label = QLabel("issues read")
        self.home_stat_bookmarks_label = QLabel("bookmarks")
        for caption in (self.home_stat_saved_label, self.home_stat_read_label, self.home_stat_bookmarks_label):
            caption.setObjectName("summaryCaption")
        for number, caption in ((self.home_stat_saved, self.home_stat_saved_label), (self.home_stat_read, self.home_stat_read_label), (self.home_stat_bookmarks, self.home_stat_bookmarks_label)):
            pair = QHBoxLayout()
            pair.setSpacing(6)
            pair.addWidget(number)
            pair.addWidget(caption)
            summary_layout.addLayout(pair)
        summary_layout.addStretch(1)
        reading_link = QPushButton("Reading list")
        reading_link.setObjectName("textButton")
        reading_link.clicked.connect(lambda: self.show_page(self.READING_LIST))
        bookmark_link = QPushButton("Bookmarks")
        bookmark_link.setObjectName("textButton")
        bookmark_link.clicked.connect(lambda: self.show_page(self.BOOKMARKS))
        summary_layout.addWidget(reading_link)
        summary_layout.addWidget(bookmark_link)
        hl.addWidget(summary_line)

        local_strip = QFrame()
        local_strip.setObjectName("localStrip")
        ls = QHBoxLayout(local_strip)
        ls.setContentsMargins(0, 11, 0, 11)
        ls.setSpacing(12)
        local_tag = QLabel("LOCAL")
        local_tag.setObjectName("eyebrow")
        self.home_local_title = QLabel("No local comic open")
        self.home_local_title.setTextFormat(Qt.TextFormat.PlainText)
        self.home_local_title.setObjectName("localTitle")
        self.home_folder_label = QLabel("Choose a folder to build your local library")
        self.home_folder_label.setTextFormat(Qt.TextFormat.PlainText)
        self.home_folder_label.setObjectName("muted")
        self.home_local_btn = QPushButton("Open library")
        self.home_local_btn.setObjectName("textButton")
        self.home_local_btn.clicked.connect(self.resume_local)
        choose = QPushButton("Choose folder")
        choose.setObjectName("textButton")
        choose.clicked.connect(self.choose_folder)
        ls.addWidget(local_tag)
        ls.addWidget(self.home_local_title)
        ls.addWidget(self.home_folder_label, 1)
        ls.addWidget(self.home_local_btn)
        ls.addWidget(choose)
        hl.addWidget(local_strip)

        recent_head = QHBoxLayout()
        recent_title = QLabel("Recently read")
        recent_title.setObjectName("sectionHeading")
        self.recent_hint = QLabel("Your latest issues")
        self.recent_hint.setObjectName("muted")
        recent_head.addWidget(recent_title)
        recent_head.addWidget(self.recent_hint)
        recent_head.addStretch(1)
        self.clear_recent_btn = QPushButton("Clear")
        self.clear_recent_btn.setObjectName("textButton")
        self.clear_recent_btn.setToolTip("Clear recent history without deleting reading progress")
        self.clear_recent_btn.clicked.connect(self.clear_recent_history)
        recent_head.addWidget(self.clear_recent_btn)
        hl.addLayout(recent_head)

        self.home_recent_list = HorizontalShelf()
        self.home_recent_list.setObjectName("recentShelf")
        self.home_recent_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.home_recent_list.setFlow(QListWidget.Flow.LeftToRight)
        self.home_recent_list.setWrapping(False)
        self.home_recent_list.setMovement(QListWidget.Movement.Static)
        self.home_recent_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.home_recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.home_recent_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.home_recent_list.setFixedHeight(354)
        self.home_recent_list.setSpacing(16)
        self.home_recent_list.itemClicked.connect(self.open_recent_issue)
        hl.addWidget(self.home_recent_list)

        quick_strip = QFrame()
        quick_strip.setObjectName("quickAccessStrip")
        ql = QHBoxLayout(quick_strip)
        ql.setContentsMargins(0, 14, 0, 4)
        ql.setSpacing(10)
        quick_title = QLabel("Your library")
        quick_title.setObjectName("sectionHeadingSmall")
        quick_note = QLabel("Pick up a saved series, browse local files, or jump to a bookmark.")
        quick_note.setObjectName("muted")
        ql.addWidget(quick_title)
        ql.addWidget(quick_note)
        ql.addStretch(1)
        quick_reading = QPushButton("Reading list")
        quick_reading.setObjectName("textButton")
        quick_reading.clicked.connect(lambda: self.show_page(self.READING_LIST))
        quick_local = QPushButton("Local library")
        quick_local.setObjectName("textButton")
        quick_local.clicked.connect(lambda: self.show_page(self.LIBRARY))
        quick_bookmarks = QPushButton("Bookmarks")
        quick_bookmarks.setObjectName("textButton")
        quick_bookmarks.clicked.connect(lambda: self.show_page(self.BOOKMARKS))
        ql.addWidget(quick_reading)
        ql.addWidget(quick_local)
        ql.addWidget(quick_bookmarks)
        hl.addWidget(quick_strip)
        hl.addStretch(1)
        self.pages.addWidget(home)

        # LIBRARY
        library = QWidget()
        library.setObjectName("libraryPage")
        ll = QVBoxLayout(library)
        ll.setContentsMargins(38, 30, 38, 30)
        ll.setSpacing(16)
        top = QHBoxLayout()
        heading = QLabel("Local library")
        heading.setObjectName("displayHeading")
        self.folder_label = QLabel("No comics folder selected")
        self.folder_label.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_label.setObjectName("muted")
        top.addWidget(heading)
        top.addStretch(1)
        top.addWidget(self.folder_label)
        self.library_fullscreen_btn = QPushButton("Fullscreen")
        self.library_fullscreen_btn.setObjectName("SecondaryButton")
        self.library_fullscreen_btn.setToolTip("Toggle fullscreen (F11)")
        self.library_fullscreen_btn.clicked.connect(self.toggle_window_fullscreen)
        top.addWidget(self.library_fullscreen_btn)
        ll.addLayout(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search local comics")
        self.search.textChanged.connect(self.filter_library)
        ll.addWidget(self.search)

        self.library_list = ClickableList()
        self.library_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.library_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.library_list.setMovement(QListWidget.Movement.Static)
        self.library_list.setObjectName("LocalLibraryGrid")
        self.library_list.setIconSize(QSize(170, 238))
        self.library_list.setGridSize(QSize(204, 292))
        self.library_list.setSpacing(12)
        self.library_list.fileActivated.connect(self.open_comic)
        ll.addWidget(self.library_list, 1)
        self.pages.addWidget(library)

        self.reader = ReaderWidget(self.progress_store, self.record_local_progress)
        self.reader.backRequested.connect(lambda: self.show_page(self.LIBRARY))
        self.pages.addWidget(self.reader)

        self.browser = BrowserWidget(self.last_batcave_url)
        self.browser.set_fit_mode(self.batcave_fit_mode, emit=False)
        self.browser.fitModeChanged.connect(self.record_batcave_fit_mode)
        self.browser.urlSaved.connect(self.record_batcave_url)
        self.browser.issueChanged.connect(self.record_batcave_issue)
        self.browser.readingModeChanged.connect(self.on_batcave_reading_mode)
        self.browser.saveToggleRequested.connect(self.toggle_saved_batcave)
        self.browser.readToggleRequested.connect(self.toggle_read_batcave)
        self.browser.autoReadDetected.connect(self.auto_mark_read_batcave)
        self.browser.issueProgress.connect(self.record_batcave_issue_progress)
        self.browser.zoomChanged.connect(self.record_batcave_zoom)
        self.browser.readingListRequested.connect(lambda: self.show_page(self.READING_LIST))
        self.browser.bookmarkRequested.connect(self.toggle_current_bookmark)
        # Keep cover discovery outside BrowserWidget to separate library
        # artwork from reader navigation.
        self.browser.web.loadFinished.connect(self._capture_live_series_cover)
        self.pages.addWidget(self.browser)

        # READING LIST
        saved_page = QWidget()
        saved_page.setObjectName("readingPage")
        rll = QVBoxLayout(saved_page)
        rll.setContentsMargins(38, 30, 38, 30)
        rll.setSpacing(16)
        top_line = QHBoxLayout()
        title_stack = QVBoxLayout()
        rheading = QLabel("Reading list")
        rheading.setObjectName("displayHeading")
        self.reading_stats = QLabel("")
        self.reading_stats.setObjectName("muted")
        title_stack.addWidget(rheading)
        title_stack.addWidget(self.reading_stats)
        top_line.addLayout(title_stack)
        top_line.addStretch(1)
        self.reading_more_btn = QPushButton("•••")
        self.reading_more_btn.setObjectName("moreButton")
        self.reading_more_btn.setToolTip("Reading list options")
        self.reading_more_menu = QMenu(self.reading_more_btn)
        self.reading_more_menu.addAction("Expand all", self.expand_all_series)
        self.reading_more_menu.addAction("Collapse all", self.collapse_all_series)
        self.reading_more_menu.addSeparator()
        self.reading_more_menu.addAction("Backup now", self.backup_now)
        self.reading_more_menu.addAction("Export data…", self.export_reader_data)
        self.reading_more_menu.addAction("Import data…", self.import_reader_data)
        self.reading_more_btn.setMenu(self.reading_more_menu)
        top_line.addWidget(self.reading_more_btn)
        rll.addLayout(top_line)

        control_bar = QFrame()
        control_bar.setObjectName("controlBar")
        cb = QHBoxLayout(control_bar)
        cb.setContentsMargins(12, 9, 12, 9)
        cb.setSpacing(8)
        self.reading_search = QLineEdit()
        self.reading_search.setPlaceholderText("Search series or issues")
        self.reading_filter = QComboBox()
        self.reading_filter.addItems(["All", "Unread", "In Progress", "Read"])
        self.reading_sort = QComboBox()
        self.reading_sort.addItems(["Recently read", "Title A–Z", "Progress"])
        sort_idx = self.reading_sort.findText(self.reading_sort_mode)
        self.reading_sort.setCurrentIndex(max(0, sort_idx))
        self.reading_search.textChanged.connect(self.refresh_reading_list)
        self.reading_filter.currentIndexChanged.connect(self.refresh_reading_list)
        self.reading_sort.currentTextChanged.connect(self.on_reading_sort_changed)
        cb.addWidget(self.reading_search, 1)
        cb.addWidget(self.reading_filter)
        cb.addWidget(self.reading_sort)
        rll.addWidget(control_bar)

        self.reading_list = QListWidget()
        self.reading_list.setObjectName("readingList")
        self.reading_list.setSpacing(2)
        self.reading_list.itemClicked.connect(self.on_reading_item_clicked)
        self.reading_list.itemDoubleClicked.connect(self.open_saved_item)
        rll.addWidget(self.reading_list, 1)

        actions = QHBoxLayout()
        open_saved = QPushButton("Open selected")
        open_saved.setObjectName("PrimaryButton")
        toggle_read = QPushButton("Toggle read")
        toggle_read.setObjectName("SecondaryButton")
        remove_saved = QPushButton("Remove")
        remove_saved.setObjectName("dangerButton")
        open_saved.clicked.connect(self.open_selected_saved)
        toggle_read.clicked.connect(self.toggle_selected_read)
        remove_saved.clicked.connect(self.remove_selected_saved)
        actions.addWidget(open_saved)
        actions.addWidget(toggle_read)
        actions.addWidget(remove_saved)
        actions.addStretch(1)
        rll.addLayout(actions)
        self.pages.addWidget(saved_page)
        self.refresh_reading_list()
        self.sync_browser_tracking()

        # SERIES DETAILS
        series_page = QWidget()
        series_page.setObjectName("seriesPage")
        sdl = QVBoxLayout(series_page)
        sdl.setContentsMargins(38, 30, 38, 30)
        sdl.setSpacing(18)
        top_actions = QHBoxLayout()
        self.series_back_btn = QPushButton("← Reading list")
        self.series_back_btn.setObjectName("textButton")
        self.series_back_btn.clicked.connect(lambda: self.show_page(self.READING_LIST))
        top_actions.addWidget(self.series_back_btn)
        top_actions.addStretch(1)
        self.series_open_web = QPushButton("Open on BatCave")
        self.series_open_web.setObjectName("ghostButton")
        self.series_open_web.clicked.connect(self.open_current_series_web)
        top_actions.addWidget(self.series_open_web)
        sdl.addLayout(top_actions)

        series_hero = QFrame()
        series_hero.setObjectName("seriesHero")
        sh = QVBoxLayout(series_hero)
        sh.setContentsMargins(26, 24, 26, 24)
        sh.setSpacing(8)
        series_label = QLabel("SERIES")
        series_label.setObjectName("eyebrow")
        self.series_title = QLabel("Series")
        self.series_title.setTextFormat(Qt.TextFormat.PlainText)
        self.series_title.setObjectName("heroTitle")
        self.series_summary = QLabel("")
        self.series_summary.setObjectName("heroMeta")
        self.series_progress = QProgressBar()
        self.series_progress.setTextVisible(True)
        self.series_progress.setObjectName("seriesProgress")
        sh.addWidget(series_label)
        sh.addWidget(self.series_title)
        sh.addWidget(self.series_summary)
        sh.addSpacing(6)
        sh.addWidget(self.series_progress)
        sdl.addWidget(series_hero)

        issues_heading = QLabel("Issues")
        issues_heading.setObjectName("sectionHeading")
        sdl.addWidget(issues_heading)
        self.series_issue_list = QListWidget()
        self.series_issue_list.setObjectName("seriesIssueList")
        self.series_issue_list.setSpacing(2)
        self.series_issue_list.itemDoubleClicked.connect(self.open_series_issue)
        sdl.addWidget(self.series_issue_list, 1)
        series_actions = QHBoxLayout()
        series_open_issue = QPushButton("Open issue")
        series_open_issue.setObjectName("primaryButton")
        series_toggle = QPushButton("Toggle read")
        series_toggle.setObjectName("ghostButton")
        series_open_issue.clicked.connect(self.open_selected_series_issue)
        series_toggle.clicked.connect(self.toggle_selected_series_issue_read)
        series_actions.addWidget(series_open_issue)
        series_actions.addWidget(series_toggle)
        series_actions.addStretch(1)
        sdl.addLayout(series_actions)
        self.pages.addWidget(series_page)

        # BOOKMARKS
        bookmarks_page = QWidget()
        bookmarks_page.setObjectName("bookmarksPage")
        bml = QVBoxLayout(bookmarks_page)
        bml.setContentsMargins(38, 30, 38, 30)
        bml.setSpacing(16)
        bm_top = QHBoxLayout()
        bm_stack = QVBoxLayout()
        bm_heading = QLabel("Bookmarks")
        bm_heading.setObjectName("displayHeading")
        self.bookmark_stats = QLabel("")
        self.bookmark_stats.setObjectName("muted")
        bm_stack.addWidget(bm_heading)
        bm_stack.addWidget(self.bookmark_stats)
        bm_top.addLayout(bm_stack)
        bm_top.addStretch(1)
        self.bookmark_remove_top = QPushButton("Remove selected")
        self.bookmark_remove_top.setObjectName("dangerButton")
        self.bookmark_remove_top.setToolTip("Remove the selected bookmark")
        self.bookmark_remove_top.clicked.connect(self.remove_selected_bookmark)
        bm_top.addWidget(self.bookmark_remove_top)
        bml.addLayout(bm_top)
        self.bookmark_search = QLineEdit()
        self.bookmark_search.setPlaceholderText("Search title, issue or note")
        self.bookmark_search.textChanged.connect(self.refresh_bookmarks)
        bml.addWidget(self.bookmark_search)
        self.bookmark_list = QListWidget()
        self.bookmark_list.setObjectName("bookmarkList")
        self.bookmark_list.setSpacing(0)
        self.bookmark_list.itemDoubleClicked.connect(self.open_bookmark)
        bml.addWidget(self.bookmark_list, 1)
        bm_actions = QHBoxLayout()
        bm_open = QPushButton("Open bookmark")
        bm_open.setObjectName("primaryButton")
        bm_note = QPushButton("Edit note")
        bm_note.setObjectName("ghostButton")
        bm_open.clicked.connect(lambda: self.open_bookmark(self.bookmark_list.currentItem()))
        bm_note.clicked.connect(self.edit_selected_bookmark_note)
        bm_actions.addWidget(bm_open)
        bm_actions.addWidget(bm_note)
        bm_actions.addStretch(1)
        bml.addLayout(bm_actions)
        self.pages.addWidget(bookmarks_page)
        self.refresh_bookmarks()

        # Window-level shortcuts work even when the embedded web page has focus.
        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.esc_shortcut.activated.connect(self.handle_escape)
        self.f11_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.f11_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.f11_shortcut.activated.connect(self.toggle_window_fullscreen)

        if self.sidebar_collapsed:
            self.set_sidebar_collapsed(True, save=False)

    def _make_continue_card(self, title, subtitle, button_text):
        frame = QFrame()
        frame.setObjectName("continueCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setTextFormat(Qt.TextFormat.PlainText)
        t.setObjectName("cardTitle")
        s = QLabel(subtitle)
        s.setTextFormat(Qt.TextFormat.PlainText)
        s.setWordWrap(True)
        s.setObjectName("muted")
        b = QPushButton(button_text)
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addStretch(1)
        lay.addWidget(b)
        return frame, s, b

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        if (index == self.BATCAVE and self.browser.reading_mode
                and is_batcave_reader_url(self.browser.web.url())):
            self.on_batcave_reading_mode(True)
        elif index != self.BATCAVE:
            prior = getattr(self, "_sidebar_before_reading", self.sidebar_collapsed)
            self.set_sidebar_collapsed(prior, save=False)
        for btn in (self.home_btn, self.lib_btn, self.web_btn, self.saved_btn, self.bookmarks_btn):
            btn.setChecked(False)
        if index == self.HOME:
            self.home_btn.setChecked(True)
            self.refresh_home()
        elif index == self.LIBRARY:
            self.lib_btn.setChecked(True)
            self.scan_library()
        elif index == self.BATCAVE:
            self.web_btn.setChecked(True)
        elif index == self.READING_LIST:
            self.saved_btn.setChecked(True)
            self.refresh_reading_list()
        elif index == self.BOOKMARKS:
            self.bookmarks_btn.setChecked(True)
            self.refresh_bookmarks()
        elif index == self.SERIES_DETAILS:
            self.saved_btn.setChecked(True)
            self.refresh_series_details()
        if self.isFullScreen():
            self._set_reader_fullscreen_chrome(True)

    def apply_style(self):
        # Keep reader/browser mechanics separate from presentation styling.
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background:#0E1015;
                color:#F4F1EB;
                font-family:"Segoe UI", sans-serif;
                font-size:14px;
            }
            QToolTip {
                background:#161922; color:#F4F1EB; border:1px solid #303544;
                padding:6px 8px;
            }

            #Sidebar, #MiniSidebar {
                background:#11141B;
                border-right:1px solid #232734;
            }
            #brand { font-size:23px; font-weight:900; letter-spacing:2px; color:#FFF9F1; }
            #brandMark { color:#FF5722; font-size:19px; font-weight:900; letter-spacing:-2px; }
            #brandSub, #navSection, #eyebrow {
                color:#7F8796; font-size:10px; font-weight:800; letter-spacing:1.8px;
            }
            #navSection { margin:8px 0 5px 7px; }
            #navButton {
                text-align:left; background:transparent; border:none; border-left:3px solid transparent;
                border-radius:7px; padding:11px 13px; color:#B8C0CC; font-weight:600;
            }
            #navButton:hover { color:#FFFFFF; background:#171B24; }
            #navButton:checked { color:#FFFFFF; background:#191E28; border-left:3px solid #FF5722; }
            #sideUtility {
                text-align:left; background:transparent; border:none; padding:9px 11px;
                color:#929CAA; font-weight:550;
            }
            #sideUtility:hover { color:#F6F3EE; background:#171B24; }
            #sidebarToggle { background:transparent; border:none; color:#717986; text-align:left; padding:8px 6px; }
            #versionLabel { color:#4F5662; font-size:10px; padding-left:6px; }
            #miniExpand { background:transparent; border:none; color:#FF5722; font-size:20px; padding:10px 0; }
            #miniNav { background:transparent; border:none; color:#86909F; font-size:15px; padding:10px 0; }
            #miniNav:hover { color:#FFFFFF; background:#171B24; }

            #displayHeading { font-size:36px; font-weight:800; letter-spacing:-0.7px; color:#FFF9F2; }
            #heading { font-size:28px; font-weight:800; color:#FFF9F2; }
            #sectionHeading { font-size:20px; font-weight:750; color:#F5F1EA; }
            #sectionHeadingSmall { font-size:15px; font-weight:700; color:#E9E5DE; }
            #muted, #heroMeta, #readingMeta { color:#8F99A9; }
            #heroTitle { font-size:27px; font-weight:750; color:#FFF9F2; }
            #heroStatus { color:#FF7A52; font-size:10px; font-weight:850; letter-spacing:1.1px; }
            #localTitle { font-size:14px; font-weight:650; color:#E7EBF0; }
            #summaryNumber { font-size:18px; font-weight:800; color:#FFF8EF; }
            #summaryCaption { font-size:11px; color:#8490A0; }

            QPushButton {
                background:#161922; color:#E8ECF2; border:1px solid #2A2F3C;
                border-radius:7px; padding:8px 12px; font-weight:600;
            }
            QPushButton:hover { background:#1B1F2A; border-color:#3B4353; color:#FFFFFF; }
            QPushButton:pressed { background:#12151C; }
            QPushButton:disabled { color:#5F6672; background:#12151A; border-color:#1F232C; }
            #PrimaryButton, #primaryButton {
                background:#FF5722; color:#170D09; border:1px solid #FF5722;
                font-weight:800; padding:10px 17px;
            }
            #PrimaryButton:hover, #primaryButton:hover { background:#FF6737; border-color:#FF6737; }
            #SecondaryButton, #ghostButton {
                background:transparent; border:1px solid #343A48; color:#D7DDE5;
            }
            #SecondaryButton:hover, #ghostButton:hover { border-color:#596477; background:#151922; }
            #TextButton, #textButton { background:transparent; border:none; color:#9CA6B4; padding:7px 7px; }
            #TextButton:hover, #textButton:hover { color:#FF7A52; background:transparent; }
            #dangerButton { background:transparent; border:1px solid #57363C; color:#E58A96; }
            #dangerButton:hover { background:#201519; border-color:#85505A; }
            #moreButton { background:transparent; border:1px solid #2B303C; min-width:42px; padding:7px 10px; font-size:17px; }
            #moreButton:hover { background:#171B23; border-color:#475164; }

            QMenu { background:#141821; color:#E8E4DE; border:1px solid #2C3240; padding:6px; }
            QMenu::item { padding:8px 24px 8px 10px; border-radius:5px; }
            QMenu::item:selected { background:#1C222D; color:#FFFFFF; }
            QMenu::separator { height:1px; background:#29303A; margin:5px 8px; }

            QLineEdit, QComboBox {
                background:#12151C; border:1px solid #282D39; border-radius:7px;
                padding:9px 11px; color:#E9EDF2;
                selection-background-color:#FF5722; selection-color:#160D09;
            }
            QLineEdit:focus, QComboBox:focus { border-color:#505A6C; }
            QComboBox::drop-down { border:none; width:24px; }

            #HeroCard {
                background:#161922;
                border:1px solid #232734;
                border-radius:14px;
            }
            #HeroBody { background:transparent; }
            #CoverThumbnail {
                background:#11141B;
                border:1px solid #292F3C;
                border-radius:7px;
            }
            #HeroCard > #CoverThumbnail { margin:16px 0 16px 16px; }
            #heroProgress {
                border:none; background:#292E39; height:4px; border-radius:2px;
            }
            #heroProgress::chunk { background:#FF5722; border-radius:2px; }
            #librarySummary { background:transparent; border:none; border-bottom:1px solid #20242E; }
            #localStrip { background:transparent; border:none; border-bottom:1px solid #20242E; border-radius:0; }
            #quickAccessStrip { background:transparent; border-top:1px solid #20242E; border-bottom:none; }

            QListWidget { background:transparent; border:none; outline:none; }
            QListWidget::item:selected { background:transparent; color:#FFFFFF; }
            #recentShelf { background:transparent; border:none; }
            #recentShelf::item { background:transparent; border:none; padding:0; margin:0; }
            #ComicCard {
                background:#161922;
                border:1px solid #232734;
                border-radius:10px;
            }
            #ComicCard:hover { background:#191D27; border-color:#343B4B; }
            #ComicCard #CoverThumbnail { background:#101319; border:1px solid #252B36; border-radius:6px; }
            #ComicCardTitle { color:#F6F2EC; font-size:15px; font-weight:720; }
            #ComicCardMeta { color:#8D98A8; font-size:11px; }
            #ComicCardAge { color:#697383; font-size:10px; }
            #ComicCardState { font-size:11px; font-weight:700; }
            #ComicCardState[state="read"] { color:#6BC58C; }
            #ComicCardState[state="progress"] { color:#FF7650; }
            #ComicCardState[state="unread"] { color:#8E98A6; }
            #CardProgress { border:none; background:#282D37; height:4px; border-radius:2px; }
            #CardProgress::chunk { background:#FF5722; border-radius:2px; }
            #CardProgress[state="read"]::chunk { background:#5DBB7D; }

            #controlBar { background:transparent; border:none; border-bottom:1px solid #232734; border-radius:0; }
            #readingList, #seriesIssueList { background:transparent; }
            #ReadingListRow {
                background:transparent; border:none; border-bottom:1px solid #20242E; border-radius:0;
            }
            #ReadingListRow[expanded="true"] {
                background:#12161E; border:none; border-left:3px solid #FF5722;
                border-bottom:1px solid #232734;
            }
            #IssueRow {
                background:#10131A; border:none; border-left:2px solid #272D39;
                border-bottom:1px solid #1B1F28; border-radius:0;
            }
            #readingSeriesTitle { font-size:16px; font-weight:750; color:#F5F1EA; }
            #readingIssueTitle { font-size:13px; font-weight:680; color:#E7EBF0; }
            #readingIssueNumber { color:#929DAD; font-size:12px; font-weight:780; }
            #seriesChevron { color:#8993A2; font-size:17px; font-weight:700; }
            #seriesState, #issueState { background:transparent; border:none; font-size:11px; font-weight:700; }
            #seriesState[state="progress"], #issueState[state="progress"] { color:#FF7650; }
            #seriesState[state="read"], #issueState[state="read"] { color:#68C58A; }
            #seriesState[state="unread"], #issueState[state="unread"] { color:#8993A2; }
            #seriesRowProgress { border:none; background:#282D37; height:4px; border-radius:2px; margin-top:4px; }
            #seriesRowProgress::chunk { background:#FF5722; border-radius:2px; }

            #seriesHero { background:#161922; border:1px solid #232734; border-radius:12px; }
            #seriesProgress { border:none; background:#292E39; height:12px; border-radius:3px; color:#D6DCE4; text-align:center; }
            #seriesProgress::chunk { background:#FF5722; border-radius:3px; }

            #LocalLibraryGrid::item { padding:8px; margin:0; border:none; color:#DCE1E7; }
            #LocalLibraryGrid::item:hover { background:#171B23; }
            #LocalLibraryGrid::item:selected { background:#1B202A; border-bottom:2px solid #FF5722; }

            #bookmarkList { background:transparent; border-top:1px solid #232734; border-bottom:1px solid #232734; }
            #bookmarkList::item { padding:11px 8px; border-bottom:1px solid #1B1F28; border-radius:0; }

            #readerBar, #readerFooter, #browserBar, #readerControlStrip {
                background:#12151C; border-bottom:1px solid #232734;
            }
            #browserIconButton { background:transparent; border:1px solid transparent; color:#A7B0BC; padding:7px 8px; }
            #browserIconButton:hover { background:#191D25; border-color:#303743; color:#FFFFFF; }
            #browserHomeButton { background:transparent; border:none; color:#F0ECE6; font-weight:720; padding:7px 10px; }
            #browserToolButton { background:#161922; border:1px solid #2D333E; color:#CFD6DE; padding:7px 9px; }
            #browserToolButton:hover, #browserToolButton:checked { border-color:#657080; color:#FFFFFF; background:#1B2029; }
            #issueTools { background:transparent; border:none; }
            #browserStatus { color:#777F8B; font-size:12px; padding-left:8px; }
            #readerFooter { border-top:1px solid #232734; border-bottom:none; }
            #readerNav { background:#12151C; border:none; border-radius:0; }
            #readerNavButton, #readerPageSelector { background:#161922; border:1px solid #2D333E; border-radius:7px; }
            #readerNavButton:hover { border-color:#596674; background:#1B2029; }
            #readerHandle { background:#FF5722; color:#130C08; border:none; border-radius:5px; padding:0; font-weight:900; }
            #readerPageSelector { padding:6px 9px; }
            #readerTitle { font-weight:720; }

            QScrollArea { border:none; background:#07090D; }
            QProgressBar { border:none; background:#292E39; color:#D5DBE3; }
            QProgressBar::chunk { background:#FF5722; }
            QScrollBar:vertical {
                background:#0E1015; width:8px; margin:2px 1px 2px 1px;
            }
            QScrollBar::handle:vertical {
                background:#343A46; min-height:28px; border-radius:4px;
            }
            QScrollBar::handle:vertical:hover { background:#465063; }
            QScrollBar:add-line:vertical, QScrollBar:sub-line:vertical { height:0; }
            QScrollBar:add-page:vertical, QScrollBar:sub-page:vertical { background:transparent; }
            QScrollBar:horizontal {
                background:#0E1015; height:8px; margin:1px 2px 1px 2px;
            }
            QScrollBar::handle:horizontal { background:#343A46; min-width:28px; border-radius:4px; }
            QScrollBar::handle:horizontal:hover { background:#465063; }
            QScrollBar:add-line:horizontal, QScrollBar:sub-line:horizontal { width:0; }
            QScrollBar:add-page:horizontal, QScrollBar:sub-page:horizontal { background:transparent; }
        """)

    def _series_palette(self, text):
        palettes = [
            ("#1b2740", "#223759", "#ff6b45"),
            ("#1b2f35", "#23454c", "#57d3c0"),
            ("#2a2344", "#3a2e5f", "#ff9b6a"),
            ("#2b2032", "#3d2d45", "#f06ca7"),
            ("#1d2d24", "#294135", "#8dd17e"),
            ("#312418", "#4a3321", "#ffb36a"),
        ]
        seed = (text or "Paneleo").encode("utf-8", "ignore")
        idx = hashlib.sha1(seed).digest()[0] % len(palettes)
        return palettes[idx]

    def _prime_cover_metadata_from_library(self):
        """Restore validated BatCave poster metadata for the thumbnail cache."""
        try:
            for section in ("issues", "saved"):
                rows = self.batcave_library.get(section, {})
                if not isinstance(rows, dict):
                    continue
                for key, data in rows.items():
                    if not isinstance(data, dict):
                        continue
                    title = data.get("series") or self._clean_batcave_title(data.get("title", ""), data.get("url", key))
                    series = self._series_name_from_title(title)
                    cache_key = (series or "").strip().lower()
                    if not cache_key:
                        continue
                    page_url = data.get("cover_page_url", "")
                    image_url = data.get("cover_image_url", "")
                    if is_allowed_batcave_url(page_url):
                        self._cover_page_cache.setdefault(cache_key, str(page_url))
                    if is_allowed_batcave_asset_url(image_url):
                        self._cover_live_urls.setdefault(cache_key, str(image_url))
        except Exception:
            pass

    def _remember_cover_metadata(self, series_name, page_url="", image_url=""):
        """Persist only BatCave cover URL metadata so artwork can reload next launch."""
        series_name = (series_name or "").strip()
        key = series_name.lower()
        if not key:
            return
        page_url = canonical_url(page_url) if is_allowed_batcave_url(page_url) else ""
        image_url = QUrl(str(image_url)).toString() if is_allowed_batcave_asset_url(image_url) else ""
        changed = False
        if page_url:
            self._cover_page_cache[key] = page_url
        if image_url:
            self._cover_live_urls[key] = image_url
        for section in ("issues", "saved"):
            rows = self.batcave_library.get(section, {})
            if not isinstance(rows, dict):
                continue
            for raw_key, data in rows.items():
                if not isinstance(data, dict):
                    continue
                row_title = data.get("series") or self._clean_batcave_title(data.get("title", ""), data.get("url", raw_key))
                row_series = self._series_name_from_title(row_title).strip().lower()
                if row_series != key:
                    continue
                if page_url and data.get("cover_page_url") != page_url:
                    data["cover_page_url"] = page_url
                    changed = True
                if image_url and data.get("cover_image_url") != image_url:
                    data["cover_image_url"] = image_url
                    changed = True
        if changed:
            save_json(BATCAVE_LIBRARY_FILE, self.batcave_library)

    def _latest_issue_url_for_series(self, series_name):
        target = (series_name or "").strip().lower()
        if not target:
            return ""
        best = (0, "")
        for key, data in self.batcave_library.get("issues", {}).items():
            if not isinstance(data, dict):
                continue
            title = data.get("series") or self._clean_batcave_title(data.get("title", ""), data.get("url", key))
            if self._series_name_from_title(title).strip().lower() != target:
                continue
            url = data.get("url", key)
            if not is_allowed_batcave_url(url):
                continue
            when = int(data.get("last_opened", 0) or 0)
            if when >= best[0]:
                best = (when, str(url))
        return best[1]

    def _series_saved_url(self, series_name):
        target = (series_name or "").strip().lower()
        if not target:
            return ""
        for key, data in self.batcave_library.get("saved", {}).items():
            if not isinstance(data, dict):
                continue
            url = data.get("url", key)
            if not is_allowed_batcave_url(url):
                continue
            title = self._clean_batcave_title(data.get("title", ""), url)
            if self._series_name_from_title(title).strip().lower() == target:
                return str(url)
        return ""

    def _init_cover_session(self):
        """Mirror BatCave session cookies into the lightweight cover fetcher only."""
        try:
            store = self.browser.web.page().profile().cookieStore()
            store.cookieAdded.connect(self._cover_cookie_added)
            store.cookieRemoved.connect(self._cover_cookie_removed)
            store.loadAllCookies()
        except Exception:
            pass

    def _cover_cookie_added(self, cookie):
        try:
            domain = str(cookie.domain() or "").lower().lstrip(".")
            if domain and not (domain == "batcave.biz" or domain.endswith(".batcave.biz")):
                return
            name = bytes(cookie.name()).decode("latin-1", "ignore")
            value = bytes(cookie.value()).decode("latin-1", "ignore")
            if not name:
                return
            self._cover_cookie_pairs[name] = value
            if not self._cover_cookie_refresh_scheduled:
                self._cover_cookie_refresh_scheduled = True
                QTimer.singleShot(450, self._retry_covers_after_cookie_sync)
        except Exception:
            pass

    def _cover_cookie_removed(self, cookie):
        try:
            name = bytes(cookie.name()).decode("latin-1", "ignore")
            if name:
                self._cover_cookie_pairs.pop(name, None)
        except Exception:
            pass

    def _retry_covers_after_cookie_sync(self):
        self._cover_cookie_refresh_scheduled = False
        if not self._cover_cookie_pairs:
            return
        self._cover_failed.clear()
        # Existing labels are already registered as waiters. Do not rebuild the
        # Home page here; just resume the queued in-page cover fetches.
        QTimer.singleShot(0, self._start_next_webengine_cover)

    def _set_cover_pixmap(self, label, pixmap):
        try:
            if label is None or pixmap is None or pixmap.isNull():
                return
            target = label.size()
            if target.width() < 2 or target.height() < 2:
                return
            scaled = pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - target.width()) // 2)
            y = max(0, (scaled.height() - target.height()) // 2)
            cropped = scaled.copy(x, y, target.width(), target.height())
            label.setPixmap(cropped)
            label.show()
        except RuntimeError:
            # The shelf may have been refreshed while a network request was in flight.
            pass
        except Exception:
            pass

    def _prepare_cover_request(self, url, referer=""):
        request = QNetworkRequest(QUrl(url))
        try:
            ua = self.browser.web.page().profile().httpUserAgent() if hasattr(self, "browser") else ""
        except Exception:
            ua = ""
        if not ua:
            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
        request.setRawHeader(b"User-Agent", ua.encode("utf-8", "ignore"))
        request.setRawHeader(b"Accept-Language", b"en-US,en;q=0.9")
        try:
            target = QUrl(url)
            host = target.host().lower().rstrip(".")
            if host == "batcave.biz" or host.endswith(".batcave.biz"):
                cookies = "; ".join(f"{k}={v}" for k, v in self._cover_cookie_pairs.items())
                if cookies:
                    request.setRawHeader(b"Cookie", cookies.encode("latin-1", "ignore"))
        except Exception:
            pass
        if referer and is_allowed_batcave_url(referer):
            request.setRawHeader(b"Referer", str(referer).encode("utf-8", "ignore"))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        return request

    def _cover_request_failed(self, key, cooldown=25):
        self._cover_page_pending.discard(key)
        self._cover_image_pending.discard(key)
        self._cover_waiters.pop(key, None)
        self._cover_failed[key] = time.monotonic() + max(1, int(cooldown))
        if len(self._cover_failed) > 64:
            oldest = min(self._cover_failed, key=self._cover_failed.get)
            self._cover_failed.pop(oldest, None)

    def _cover_failure_active(self, key):
        until = float(self._cover_failed.get(key, 0) or 0)
        if until <= time.monotonic():
            self._cover_failed.pop(key, None)
            return False
        return True

    def _extract_cover_candidates(self, html_text):
        # BatCave uses lazy-loaded posters in several layouts. Rank metadata,
        # data-src/data-original/srcset and ordinary image tags instead of
        # requiring the words "poster" or "cover" to appear beside src.
        candidates, seen = [], set()

        def add(value, score=0):
            value = html_lib.unescape(str(value or "").strip())
            if not value or value.startswith(("data:", "javascript:", "#")):
                return
            if "," in value and any(token in value for token in (" 1x", " 2x", "w,")):
                value = value.split(",", 1)[0].strip().split()[0]
            elif " " in value and value.lower().split(" ")[-1].endswith(("w", "x")):
                value = value.split()[0]
            low = value.lower()
            if low.endswith((".svg", ".gif")) or "placeholder" in low or "avatar" in low:
                score -= 25
            if value not in seen:
                seen.add(value)
                candidates.append((score, value))

        # BatCave series pages expose the canonical poster explicitly. The
        # preload is the most reliable signal and precedes recommendation posters.
        for tag in re.findall(r'<link\b[^>]*>', html_text, flags=re.I | re.S):
            attrs = {}
            for am in re.finditer(r'([:\w-]+)\s*=\s*(["\'])(.*?)\2', tag, flags=re.I | re.S):
                attrs[am.group(1).lower()] = html_lib.unescape(am.group(3).strip())
            rel = attrs.get("rel", "").lower()
            asset_type = attrs.get("as", "").lower()
            href = attrs.get("href", "")
            if "preload" in rel and asset_type == "image" and "/uploads/posts/poster/" in href.lower():
                add(href, 320)

        # JSON-LD ComicSeries uses image + thumbnailUrl for the same canonical poster.
        for pattern in (
            r'["\']thumbnailUrl["\']\s*:\s*["\']([^"\']+/uploads/posts/poster/[^"\']+)',
            r'["\']image["\']\s*:\s*["\']([^"\']+/uploads/posts/poster/[^"\']+)',
        ):
            for m in re.finditer(pattern, html_text, flags=re.I | re.S):
                add(m.group(1), 300)

        # Last exact fallback: BatCave poster asset paths have a stable directory.
        for m in re.finditer(r'((?:https://(?:www\.)?batcave\.biz)?/uploads/posts/poster/[^"\'<>\s]+)', html_text, flags=re.I):
            add(m.group(1), 260)

        for pattern, score in (
            (r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)', 100),
            (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::secure_url)?["\']', 100),
            (r'<meta[^>]+(?:property|name)=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', 95),
            (r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)', 95),
            (r'["\']image["\']\s*:\s*["\']([^"\']+)["\']', 85),
        ):
            for m in re.finditer(pattern, html_text, flags=re.I | re.S):
                add(m.group(1), score)

        h1_pos = html_text.lower().find("<h1")
        for m in re.finditer(r'<img\b[^>]*>', html_text, flags=re.I | re.S):
            tag = m.group(0)
            attrs = {}
            for am in re.finditer(r'([:\w-]+)\s*=\s*(["\'])(.*?)\2', tag, flags=re.I | re.S):
                attrs[am.group(1).lower()] = html_lib.unescape(am.group(3).strip())
            blob = " ".join((attrs.get("class", ""), attrs.get("id", ""), attrs.get("alt", ""), attrs.get("title", ""))).lower()
            score = 0
            if "poster" in blob: score += 75
            if "cover" in blob: score += 65
            if "comic" in blob: score += 20
            if any(bad in blob for bad in ("avatar", "user", "comment", "logo", "icon", "character")): score -= 70
            if h1_pos >= 0 and m.start() > h1_pos and m.start() - h1_pos < 18000: score += 22
            try:
                w = int(re.sub(r'\D', '', attrs.get("width", "0")) or 0)
                h = int(re.sub(r'\D', '', attrs.get("height", "0")) or 0)
                if h > w and h >= 180: score += 18
            except Exception:
                pass
            for attr, bonus in (("data-src", 12), ("data-original", 12), ("data-lazy-src", 12), ("data-srcset", 10), ("srcset", 8), ("src", 4)):
                if attrs.get(attr):
                    add(attrs[attr], score + bonus)
            style = attrs.get("style", "")
            bg = re.search(r'background(?:-image)?\s*:\s*url\(["\']?([^"\')]+)', style, flags=re.I)
            if bg:
                add(bg.group(1), score + 20)
        candidates.sort(key=lambda row: row[0], reverse=True)
        return [value for score, value in candidates if score > -20]

    def _cover_browser_context_loaded(self, ok):
        """Start queued cover work only after a real BatCave document exists."""
        try:
            self._cover_context_ready = bool(ok and is_allowed_batcave_url(self.browser.web.url()))
        except Exception:
            self._cover_context_ready = False
        if self._cover_context_ready:
            QTimer.singleShot(0, self._start_next_webengine_cover)
        else:
            QTimer.singleShot(200, lambda: self._probe_cover_browser_context(0))

    def _probe_cover_browser_context(self, attempt=0):
        """Race-free startup probe for the existing WebEngine document.

        QWebEngineView.url() can report the target URL while Chromium is still
        replacing the initial about:blank document. Running a cover job at that
        moment loses the JS result during navigation. We confirm location.href
        from JavaScript before allowing queued cover work to start.
        """
        if self._cover_context_ready or self._cover_context_probe_pending:
            return
        if not hasattr(self, "browser"):
            return
        if attempt > 48:
            return
        self._cover_context_probe_pending = True
        js = "(() => { try { return String(location.href || ''); } catch(e) { return ''; } })();"
        def got(href, n=attempt):
            self._cover_context_probe_pending = False
            if isinstance(href, str) and is_allowed_batcave_url(href):
                self._cover_context_ready = True
                QTimer.singleShot(0, self._start_next_webengine_cover)
                return
            QTimer.singleShot(250, lambda: self._probe_cover_browser_context(n + 1))
        try:
            self.browser.web.page().runJavaScript(js, got)
        except Exception:
            self._cover_context_probe_pending = False
            QTimer.singleShot(250, lambda: self._probe_cover_browser_context(attempt + 1))

    def _cover_cache_path(self, series_name):
        key = (series_name or "").strip().lower()
        if not key:
            return None
        digest = hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()[:40]
        return BATCAVE_COVER_CACHE_DIR / f"{digest}.jpg"

    def _load_cached_cover(self, series_name):
        path = self._cover_cache_path(series_name)
        if path is None or not path.is_file():
            return QPixmap()
        image = safe_load_qimage(path)
        if image.isNull():
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return QPixmap()
        try:
            os.utime(path, None)
        except Exception:
            pass
        return QPixmap.fromImage(image)

    def _save_cached_cover(self, series_name, pixmap):
        """Persist only a small UI thumbnail; never comic reader pages."""
        path = self._cover_cache_path(series_name)
        if path is None or pixmap is None or pixmap.isNull():
            return
        tmp = None
        try:
            image = pixmap.toImage()
            if image.isNull():
                return
            image = image.scaled(
                QSize(360, 540),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            tmp = path.with_suffix(".tmp.jpg")
            if not image.save(str(tmp), "JPG", 88):
                return
            os.replace(tmp, path)
            files = sorted(
                (f for f in BATCAVE_COVER_CACHE_DIR.glob("*.jpg") if f.is_file()),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for old in files[MAX_BATCAVE_COVER_CACHE_ITEMS:]:
                try:
                    old.unlink()
                except Exception:
                    pass
        except Exception:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    def _accept_cover_pixmap(self, series_name, pixmap, page_url="", image_url=""):
        key = (series_name or "").strip().lower()
        if not key or pixmap is None or pixmap.isNull():
            return False
        self._cover_pixmaps[key] = pixmap
        while len(self._cover_pixmaps) > 32:
            self._cover_pixmaps.pop(next(iter(self._cover_pixmaps)), None)
        self._cover_failed.pop(key, None)
        self._save_cached_cover(series_name, pixmap)
        if page_url or image_url:
            self._remember_cover_metadata(series_name, page_url, image_url)
        waiters = self._cover_waiters.pop(key, [])
        for label in waiters:
            self._set_cover_pixmap(label, pixmap)
        return True

    def _queue_webengine_cover(self, series_name, page_url):
        """Fetch BatCave artwork inside the existing embedded browser context.

        Beta 8 used a second QWebEnginePage for background artwork migration.
        On Windows that page could briefly surface as a tiny blank Paneleo
        window. 8.1 never creates or navigates a second page.
        """
        key = (series_name or "").strip().lower()
        if not key:
            return False
        live = self._cover_live_urls.get(key, "")
        if not is_allowed_batcave_url(page_url):
            if is_allowed_batcave_asset_url(live):
                page_url = BATCAVE_URL
            else:
                return False
        self._remember_cover_metadata(series_name, page_url=page_url)
        if self._cover_web_active and self._cover_web_active[0] == key:
            return True
        if any(job[0] == key for job in self._cover_web_queue):
            return True
        self._cover_web_queue.append((key, str(series_name), str(page_url)))
        self._start_next_webengine_cover()
        return True

    def _start_next_webengine_cover(self):
        if self._cover_web_active is not None or not self._cover_web_queue:
            return
        if not hasattr(self, "browser"):
            return
        if not self._cover_context_ready:
            QTimer.singleShot(0, lambda: self._probe_cover_browser_context(0))
            return
        if not is_allowed_batcave_url(self.browser.web.url()):
            self._cover_context_ready = False
            QTimer.singleShot(0, lambda: self._probe_cover_browser_context(0))
            return
        key, series_name, page_url = self._cover_web_queue.pop(0)
        job_id = f"p{int(time.time()*1000)}_{hashlib.sha1((key+page_url).encode()).hexdigest()[:10]}"
        self._cover_web_active = (key, series_name, page_url, job_id)
        image_url = self._cover_live_urls.get(key, "")
        script = r'''(() => {
          const jobId=__JOB__;
          const targetUrl=__TARGET__;
          const knownImage=__IMAGE__;
          const wantedSeries=__SERIES__.toLowerCase();
          const maxBytes=__MAXBYTES__;
          window.__paneleoCoverResults=window.__paneleoCoverResults||{};
          window.__paneleoCoverResults[jobId]=null;
          const allowed=(u)=>{try{const x=new URL(u,location.href); return x.protocol==='https:' && /(^|\.)batcave\.biz$/i.test(x.hostname);}catch(e){return false;}};
          const findSeries=(doc,base)=>{
            let best='',score=-1;
            for(const [i,a] of [...doc.querySelectorAll('a[href]')].entries()){
              let u; try{u=new URL(a.getAttribute('href'),base);}catch(e){continue;}
              if(!allowed(u.href) || !/^\/\d+-[^/]+\.html$/i.test(u.pathname)) continue;
              const t=(a.textContent||'').replace(/\s+/g,' ').trim().toLowerCase();
              let s=Math.max(0,20-Math.min(20,i/10));
              if(wantedSeries && t===wantedSeries) s+=120;
              else if(wantedSeries && (t.includes(wantedSeries)||wantedSeries.includes(t))) s+=70;
              if(s>score){score=s;best=u.href;}
            }
            return best;
          };
          const findPoster=(doc,base)=>{
            const img=doc.querySelector('.page__poster img');
            let src=img && (img.getAttribute('src')||img.getAttribute('data-src')||img.getAttribute('data-original')||img.getAttribute('data-lazy-src'));
            if(src) return new URL(src,base).href;
            const preload=doc.querySelector('link[rel~="preload"][as="image"][href*="/uploads/posts/poster/"]');
            if(preload) return new URL(preload.getAttribute('href'),base).href;
            for(const node of doc.querySelectorAll('script[type="application/ld+json"]')){
              try{
                const raw=JSON.parse(node.textContent||'null');
                const rows=Array.isArray(raw)?raw:[raw];
                for(const row of rows){
                  if(!row||typeof row!=='object') continue;
                  const candidate=row.thumbnailUrl || (typeof row.image==='string'?row.image:(row.image&&row.image.url));
                  if(candidate && String(candidate).includes('/uploads/posts/poster/')) return new URL(candidate,base).href;
                }
              }catch(e){}
            }
            const fallback=[...doc.querySelectorAll('img[src],img[data-src]')].find(i=>String(i.getAttribute('src')||i.getAttribute('data-src')||'').includes('/uploads/posts/poster/'));
            return fallback ? new URL(fallback.getAttribute('src')||fallback.getAttribute('data-src'),base).href : '';
          };
          (async()=>{
            try{
              let pageUrl=targetUrl, imageUrl=knownImage;
              const getText=async(url)=>{const r=await fetch(url,{credentials:'include',cache:'force-cache'}); if(!r.ok) throw new Error('page '+r.status); return await r.text();};
              if(!imageUrl){
                let html=await getText(pageUrl);
                let doc=new DOMParser().parseFromString(html,'text/html');
                if(new URL(pageUrl).pathname.toLowerCase().startsWith('/reader/')){
                  const seriesUrl=findSeries(doc,pageUrl);
                  if(!seriesUrl) throw new Error('series page not found');
                  pageUrl=seriesUrl;
                  html=await getText(pageUrl);
                  doc=new DOMParser().parseFromString(html,'text/html');
                }
                imageUrl=findPoster(doc,pageUrl);
              }
              if(!imageUrl || !allowed(imageUrl)) throw new Error('poster not found');
              const ir=await fetch(imageUrl,{credentials:'include',cache:'force-cache'});
              if(!ir.ok) throw new Error('image '+ir.status);
              const blob=await ir.blob();
              if(!blob.size || blob.size>maxBytes) throw new Error('image size');
              const dataUrl=await new Promise((resolve,reject)=>{const fr=new FileReader(); fr.onload=()=>resolve(String(fr.result||'')); fr.onerror=()=>reject(new Error('decode')); fr.readAsDataURL(blob);});
              window.__paneleoCoverResults[jobId]=JSON.stringify({ok:true,pageUrl,imageUrl,dataUrl});
            }catch(e){
              window.__paneleoCoverResults[jobId]=JSON.stringify({ok:false,error:String(e&&e.message||e)});
            }
          })();
          return true;
        })();'''
        script = (script
            .replace('__JOB__', json.dumps(job_id))
            .replace('__TARGET__', json.dumps(page_url))
            .replace('__IMAGE__', json.dumps(image_url if is_allowed_batcave_asset_url(image_url) else ""))
            .replace('__SERIES__', json.dumps(series_name))
            .replace('__MAXBYTES__', str(MAX_COVER_IMAGE_BYTES)))
        try:
            self.browser.web.page().runJavaScript(script)
            QTimer.singleShot(180, lambda jid=job_id: self._poll_webengine_cover(jid, 0))
        except Exception:
            self._cover_web_active = None
            self._cover_request_failed(key, cooldown=5)
            QTimer.singleShot(0, self._start_next_webengine_cover)

    def _poll_webengine_cover(self, job_id, attempt):
        active = self._cover_web_active
        if active is None or len(active) < 4 or active[3] != job_id:
            return
        query = f'''(() => {{ try {{ return (window.__paneleoCoverResults||{{}})[{json.dumps(job_id)}] || ''; }} catch(e) {{ return ''; }} }})();'''
        def got(result, jid=job_id, n=attempt):
            active_now = self._cover_web_active
            if active_now is None or active_now[3] != jid:
                return
            if isinstance(result, str) and result:
                try:
                    self.browser.web.page().runJavaScript(
                        f'''try {{ delete (window.__paneleoCoverResults||{{}})[{json.dumps(jid)}]; }} catch(e) {{}}'''
                    )
                except Exception:
                    pass
                self._cover_web_data_ready(active_now[0], active_now[1], result)
                return
            if n >= 80:
                key = active_now[0]
                self._cover_web_active = None
                self._cover_request_failed(key, cooldown=6)
                QTimer.singleShot(0, self._start_next_webengine_cover)
                return
            QTimer.singleShot(150, lambda: self._poll_webengine_cover(jid, n + 1))
        try:
            self.browser.web.page().runJavaScript(query, got)
        except Exception:
            got('')

    def _cover_web_data_ready(self, key, series_name, result):
        try:
            data = json.loads(result) if isinstance(result, str) else {}
            raw = b''
            data_url = data.get('dataUrl', '') if isinstance(data, dict) else ''
            if isinstance(data, dict) and data.get('ok') and isinstance(data_url, str) and data_url.startswith('data:image/') and ',' in data_url:
                import base64
                raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
            if raw and len(raw) <= MAX_COVER_IMAGE_BYTES:
                image = safe_qimage_from_bytes(raw)
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    if not pixmap.isNull():
                        page_url = data.get('pageUrl', '')
                        image_url = data.get('imageUrl', '')
                        self._accept_cover_pixmap(series_name, pixmap, page_url, image_url)
                        return
            self._cover_request_failed(key, cooldown=6)
        except Exception:
            self._cover_request_failed(key, cooldown=6)
        finally:
            self._cover_web_active = None
            QTimer.singleShot(0, self._start_next_webengine_cover)

    def _start_persisted_cover_fetch(self, series_name, image_url, referer=""):
        """Fetch a previously validated poster URL without waiting for page navigation.

        This is the fast restart path. It uses only persisted BatCave poster
        metadata and stores the resulting small UI thumbnail through the same
        bounded cache used by live cover capture.
        """
        key = (series_name or "").strip().lower()
        if not key or key in self._cover_image_pending or not is_allowed_batcave_asset_url(image_url):
            return False
        try:
            request = self._prepare_cover_request(image_url, referer)
            request.setRawHeader(b"Accept", b"image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
            reply = self.cover_network.get(request)
            reply.setProperty("paneleo_cover_redirects", 0)
            self._cover_image_pending.add(key)
            reply.finished.connect(lambda r=reply, k=key, n=str(series_name), ref=str(referer): self._persisted_cover_finished(k, n, ref, r))
            return True
        except Exception:
            self._cover_image_pending.discard(key)
            return False

    def _persisted_cover_finished(self, key, series_name, referer, reply):
        """Complete the restart fast-path; fall back to Chromium if needed."""
        try:
            redirect = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            if redirect:
                target = redirect if isinstance(redirect, QUrl) else reply.url().resolved(QUrl(str(redirect)))
                if isinstance(redirect, QUrl) and redirect.isRelative():
                    target = reply.url().resolved(redirect)
                redirects = int(reply.property("paneleo_cover_redirects") or 0)
                if is_allowed_batcave_asset_url(target) and redirects < 2:
                    request = self._prepare_cover_request(target.toString(), referer)
                    request.setRawHeader(b"Accept", b"image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
                    nxt = self.cover_network.get(request)
                    nxt.setProperty("paneleo_cover_redirects", redirects + 1)
                    nxt.finished.connect(lambda r=nxt, k=key, n=series_name, ref=referer: self._persisted_cover_finished(k, n, ref, r))
                    return
                raise ValueError("unsafe cover redirect")
            raw = bytes(reply.readAll())
            if not raw or len(raw) > MAX_COVER_IMAGE_BYTES:
                raise ValueError("invalid cover size")
            image = safe_qimage_from_bytes(raw)
            if image.isNull():
                raise ValueError("invalid cover image")
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                raise ValueError("invalid cover pixmap")
            self._cover_image_pending.discard(key)
            self._accept_cover_pixmap(series_name, pixmap, referer, reply.url().toString())
            return
        except Exception:
            self._cover_image_pending.discard(key)
            # Keep the existing waiter labels alive and use the proven browser
            # path once Chromium is ready. Do not mark failure yet.
            page = self._cover_page_cache.get(key) or self._series_saved_url(series_name) or self._latest_issue_url_for_series(series_name)
            if is_allowed_batcave_url(page):
                self._queue_webengine_cover(series_name, page)
            else:
                self._cover_request_failed(key, cooldown=6)
        finally:
            reply.deleteLater()

    def _request_series_cover(self, series_name, label):
        # Check the memory cache first, then the bounded local thumbnail cache.
        # Missing covers are fetched inside the existing BatCave WebEngine page;
        # no second browser window/page is created.
        key = (series_name or "").strip().lower()
        if not key or label is None or self._cover_failure_active(key):
            return
        cached = self._cover_pixmaps.get(key)
        if cached is not None and not cached.isNull():
            self._set_cover_pixmap(label, cached)
            return
        disk_cached = self._load_cached_cover(series_name)
        if disk_cached is not None and not disk_cached.isNull():
            self._cover_pixmaps[key] = disk_cached
            self._set_cover_pixmap(label, disk_cached)
            return
        waiters = self._cover_waiters.setdefault(key, [])
        if not any(existing is label for existing in waiters):
            waiters.append(label)
        live = self._cover_live_urls.get(key, "")
        known_page = (self._cover_page_cache.get(key)
                      or self._series_saved_url(series_name)
                      or self._latest_issue_url_for_series(series_name))
        # Restart fast path: if previous sessions already discovered the exact
        # poster URL, fetch it immediately and populate the existing labels.
        if is_allowed_batcave_asset_url(live):
            if self._start_persisted_cover_fetch(series_name, live, known_page if is_allowed_batcave_url(known_page) else BATCAVE_URL):
                return
        if self._cover_web_active and self._cover_web_active[0] == key:
            return
        if any(job[0] == key for job in self._cover_web_queue):
            return
        known_page = (self._cover_page_cache.get(key)
                      or self._series_saved_url(series_name)
                      or self._latest_issue_url_for_series(series_name))
        if not is_allowed_batcave_url(known_page) and is_allowed_batcave_asset_url(live):
            known_page = BATCAVE_URL
        if is_allowed_batcave_url(known_page):
            self._queue_webengine_cover(series_name, known_page)
            return
        self._cover_request_failed(key, cooldown=6)

    def _cover_page_finished(self, key, page_url, reply):
        self._cover_page_pending.discard(key)
        try:
            redirect = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            if redirect:
                target = redirect if isinstance(redirect, QUrl) else reply.url().resolved(QUrl(str(redirect)))
                if isinstance(redirect, QUrl) and redirect.isRelative():
                    target = reply.url().resolved(redirect)
                redirects = int(reply.property("paneleo_cover_redirects") or 0)
                if is_allowed_batcave_url(target) and redirects < 2:
                    self._cover_page_pending.add(key)
                    request = self._prepare_cover_request(target.toString())
                    request.setRawHeader(b"Accept", b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                    next_reply = self.cover_network.get(request)
                    next_reply.setProperty("paneleo_cover_redirects", redirects + 1)
                    next_reply.finished.connect(lambda r=next_reply, k=key, u=target.toString(): self._cover_page_finished(k, u, r))
                    return
                self._cover_request_failed(key)
                return
            final_url = reply.url()
            raw = bytes(reply.readAll())
            if not is_allowed_batcave_url(final_url) or not raw or len(raw) > MAX_COVER_HTML_BYTES:
                self._cover_request_failed(key)
                return
            html_text = raw.decode("utf-8", "ignore")
            image_url = ""
            base = final_url if final_url.isValid() else QUrl(page_url)
            for candidate in self._extract_cover_candidates(html_text):
                resolved = base.resolved(QUrl(candidate))
                if is_allowed_batcave_asset_url(resolved):
                    image_url = resolved.toString()
                    break
            if not image_url:
                self._cover_request_failed(key, cooldown=10)
                return
            self._cover_live_urls[key] = image_url
            self._cover_page_cache[key] = final_url.toString()
            while len(self._cover_page_cache) > 64:
                self._cover_page_cache.pop(next(iter(self._cover_page_cache)), None)
            self._cover_image_pending.add(key)
            try:
                request = self._prepare_cover_request(image_url, final_url.toString())
                request.setRawHeader(b"Accept", b"image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
                image_reply = self.cover_network.get(request)
                image_reply.setProperty("paneleo_cover_redirects", 0)
            except Exception:
                self._cover_request_failed(key)
                return
            image_reply.finished.connect(lambda r=image_reply, k=key: self._cover_image_finished(k, r))
        finally:
            reply.deleteLater()

    def _capture_live_series_cover(self, ok):
        if not ok or not hasattr(self, "browser"):
            return
        page_url = self.browser.web.url()
        if not is_allowed_batcave_url(page_url):
            return
        if is_batcave_reader_url(page_url):
            js = r'''(() => {
              try {
                const title=(document.title||'').replace(/^Read\s+/i,'').replace(/\s+comics online.*$/i,'').trim();
                const base=title.replace(/\s+#\s*\d+.*$/i,'').trim().toLowerCase();
                let best='', score=-1;
                for(const [i,a] of [...document.querySelectorAll('a[href]')].entries()){
                  let u; try{u=new URL(a.getAttribute('href'),location.href);}catch(e){continue;}
                  if(!/(^|\.)batcave\.biz$/i.test(u.hostname)) continue;
                  if(!/^\/\d+-[^/]+\.html$/i.test(u.pathname)) continue;
                  const t=(a.textContent||'').replace(/\s+/g,' ').trim().toLowerCase();
                  let s=10+Math.max(0,20-Math.min(20,i/10));
                  if(base && t===base) s+=100; else if(base&&(t.includes(base)||base.includes(t))) s+=60;
                  if(s>score){score=s;best=u.href;}
                }
                return JSON.stringify({title,seriesUrl:best});
              }catch(e){return JSON.stringify({title:'',seriesUrl:''});}
            })();'''
            def got_reader(result):
                try:
                    data=json.loads(result) if isinstance(result,str) else {}
                except Exception:
                    return
                series=self._series_name_from_title(data.get('title',''))
                key=(series or '').strip().lower()
                series_url=data.get('seriesUrl','')
                if key and is_allowed_batcave_url(series_url):
                    self._cover_page_cache[key]=series_url
                    self._remember_cover_metadata(series, series_url, "")
                    self._cover_failed.pop(key,None)
                    if self._cover_waiters.get(key):
                        self._queue_webengine_cover(series, series_url)
            self.browser.web.page().runJavaScript(js, got_reader)
            return

        path = (page_url.path() or "").lower()
        if path in ("", "/", "/comix", "/comix/") or path.startswith(("/folder/", "/character/")):
            return
        js = r'''(() => {
          const title=(document.querySelector('h1')?.textContent||document.title||'').trim();
          let src=''; let dataUrl='';
          const poster=document.querySelector('.page__poster img');
          if(poster){
            src=poster.currentSrc||poster.getAttribute('src')||poster.getAttribute('data-src')||'';
            try{
              if(poster.complete&&poster.naturalWidth&&poster.naturalHeight){
                const maxW=360,maxH=540,scale=Math.min(1,maxW/poster.naturalWidth,maxH/poster.naturalHeight);
                const c=document.createElement('canvas'); c.width=Math.max(1,Math.round(poster.naturalWidth*scale)); c.height=Math.max(1,Math.round(poster.naturalHeight*scale));
                c.getContext('2d',{alpha:false}).drawImage(poster,0,0,c.width,c.height);
                dataUrl=c.toDataURL('image/jpeg',0.90);
              }
            }catch(e){}
          }
          if(!src){
            const preload=document.querySelector('link[rel~="preload"][as="image"][href*="/uploads/posts/poster/"]');
            if(preload) src=preload.href||preload.getAttribute('href')||'';
          }
          const related=[...document.querySelectorAll('a.poster img')].slice(0,40).map(img=>({
            title:(img.getAttribute('alt')||'').trim(),
            src:img.currentSrc||img.getAttribute('src')||img.getAttribute('data-src')||'',
            href:(img.closest('a.poster')?.href||'')
          })).filter(x=>x.title&&x.src&&x.src.includes('/uploads/posts/poster/'));
          return JSON.stringify({title,src,dataUrl,related});
        })();'''
        def got(result, source_url=page_url.toString()):
            if not isinstance(result, str):
                return
            try:
                data = json.loads(result)
            except Exception:
                return
            series = self._series_name_from_title(data.get("title", ""))
            key = (series or "").strip().lower()
            if key:
                self._cover_page_cache[key] = source_url
                self._cover_failed.pop(key, None)
                data_url = data.get('dataUrl','')
                if isinstance(data_url,str) and data_url.startswith('data:image/') and ',' in data_url:
                    try:
                        import base64
                        raw=base64.b64decode(data_url.split(',',1)[1],validate=True)
                        image=safe_qimage_from_bytes(raw)
                        if not image.isNull():
                            pix=QPixmap.fromImage(image)
                            if not pix.isNull():
                                self._accept_cover_pixmap(series, pix, source_url, "")
                    except Exception:
                        pass
                resolved = QUrl(source_url).resolved(QUrl(data.get("src", "")))
                if is_allowed_batcave_asset_url(resolved):
                    self._cover_live_urls[key] = resolved.toString()
                    self._remember_cover_metadata(series, source_url, resolved.toString())
                else:
                    self._remember_cover_metadata(series, source_url, "")
            related = data.get("related", [])
            if isinstance(related, list):
                for row in related:
                    if not isinstance(row, dict):
                        continue
                    related_series = self._series_name_from_title(row.get("title", ""))
                    related_key = (related_series or "").strip().lower()
                    related_url = QUrl(source_url).resolved(QUrl(row.get("src", "")))
                    related_page = QUrl(source_url).resolved(QUrl(row.get("href", "")))
                    if related_key and is_allowed_batcave_asset_url(related_url):
                        self._cover_live_urls.setdefault(related_key, related_url.toString())
                        if is_allowed_batcave_url(related_page):
                            self._cover_page_cache.setdefault(related_key, related_page.toString())
                        self._remember_cover_metadata(related_series, related_page.toString() if is_allowed_batcave_url(related_page) else "", related_url.toString())
                        self._cover_failed.pop(related_key, None)
        self.browser.web.page().runJavaScript(js, got)

    def _cover_image_finished(self, key, reply):
        self._cover_image_pending.discard(key)
        try:
            redirect = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            if redirect:
                target = redirect if isinstance(redirect, QUrl) else reply.url().resolved(QUrl(str(redirect)))
                if isinstance(redirect, QUrl) and redirect.isRelative():
                    target = reply.url().resolved(redirect)
                redirects = int(reply.property("paneleo_cover_redirects") or 0)
                if is_allowed_batcave_asset_url(target) and redirects < 2:
                    self._cover_image_pending.add(key)
                    try:
                        request = self._prepare_cover_request(target.toString())
                        request.setRawHeader(b"Accept", b"image/avif,image/webp,image/apng,image/*,*/*;q=0.8")
                        next_reply = self.cover_network.get(request)
                        next_reply.setProperty("paneleo_cover_redirects", redirects + 1)
                        next_reply.finished.connect(lambda r=next_reply, k=key: self._cover_image_finished(k, r))
                        return
                    except Exception:
                        pass
                self._cover_request_failed(key)
                return
            if not is_allowed_batcave_asset_url(reply.url()):
                self._cover_request_failed(key)
                return
            raw = bytes(reply.readAll())
            if not raw or len(raw) > MAX_COVER_IMAGE_BYTES:
                self._cover_request_failed(key)
                return
            image = safe_qimage_from_bytes(raw)
            if image.isNull():
                self._cover_request_failed(key)
                return
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                self._cover_request_failed(key)
                return
            self._cover_pixmaps[key] = pixmap
            while len(self._cover_pixmaps) > 32:
                self._cover_pixmaps.pop(next(iter(self._cover_pixmaps)), None)
            waiters = self._cover_waiters.pop(key, [])
            for label in waiters:
                self._set_cover_pixmap(label, pixmap)
        finally:
            reply.deleteLater()

    def resume_home_primary(self):
        mode = getattr(self, "_home_primary_mode", "browse")
        if mode == "local":
            self.resume_local()
            return
        if mode == "batcave":
            url = getattr(self, "_home_continue_url", "")
            page = int(getattr(self, "_home_continue_page", 0) or 0)
            if is_allowed_batcave_url(url):
                self.browser.resume_issue(url, page)
                self.show_page(self.BATCAVE)
                return
            self.resume_batcave()
            return
        self.open_batcave_home()

    def refresh_home(self):
        if hasattr(self, "home_cover_art"):
            self.home_cover_art.clear()
            self.home_cover_art.show()
        if self.comic_dir and Path(self.comic_dir).exists():
            self.home_folder_label.setText(str(self.comic_dir))
        else:
            self.home_folder_label.setText("Choose a folder to build your local library")

        local_available = bool(self.last_local_file and Path(self.last_local_file).exists())
        if local_available:
            name = Path(self.last_local_file).stem
            page = self.last_local_page + 1
            total = self.last_local_total
            suffix = f" · page {page}/{total}" if total else f" · page {page}"
            self.home_local_title.setText(name + suffix)
            self.home_local_btn.setText("Continue local")
        else:
            self.home_local_title.setText("No local comic open")
            self.home_local_btn.setText("Open library")

        issues = self.batcave_library.get("issues", {})
        read = self.batcave_library.get("read", {})
        latest = None
        for key, data in issues.items():
            if not isinstance(data, dict):
                continue
            url = data.get("url", key)
            if not is_allowed_batcave_url(url):
                continue
            when = int(data.get("last_opened", 0) or 0)
            if latest is None or when > latest[0]:
                latest = (when, key, data)

        self._home_continue_url = ""
        self._home_continue_page = 0

        local_is_latest = bool(
            local_available
            and (latest is None or self.last_local_opened >= latest[0])
        )

        if latest is not None and not local_is_latest:
            _, key, data = latest
            url = data.get("url", key)
            title = self._clean_batcave_title(data.get("title", ""), url)
            series_name = data.get("series") or self._series_name_from_title(title)
            issue_no = data.get("issue") or self._issue_number_from_title(title)
            current = int(data.get("current_page", 0) or 0)
            total = int(data.get("total_pages", 0) or 0)
            is_read = canonical_url(url) in read or key in read

            display_title = title
            generic = display_title.lower()
            if (not display_title or "comics online for free" in generic
                    or "read comics online" in generic or display_title.lower() == "batcave item"):
                if series_name and issue_no:
                    display_title = f"{series_name} #{issue_no}"
                else:
                    display_title = series_name or "Continue reading"

            self.home_continue_status.setText("FINISHED" if is_read else ("IN PROGRESS" if current > 1 else "READY TO READ"))
            self.home_continue_title.setText(display_title)
            detail = []
            if issue_no:
                detail.append(f"Issue #{issue_no}")
            if current and total:
                detail.append(f"Page {current} of {total}")
            self.home_continue_meta.setText("  •  ".join(detail) if detail else "Resume this issue")
            self.home_continue_submeta.setText(series_name or "BatCave")
            pct = int((current / total) * 100) if current and total else (100 if is_read else 0)
            self.home_continue_progress.setValue(max(0, min(100, pct)))
            self.home_continue_btn.setText("Open issue" if is_read else "Continue reading")
            self._home_primary_mode = "batcave"
            self._home_continue_url = url
            self._home_continue_page = current
            _, _, accent = self._series_palette(series_name or display_title)
            self.home_hero_accent.setStyleSheet(f"background:{accent}; border:none;")
            self._request_series_cover(series_name, self.home_cover_art)
        elif local_available:
            name = Path(self.last_local_file).stem
            current = self.last_local_page + 1
            total = self.last_local_total
            self.home_continue_status.setText("LOCAL COMIC")
            self.home_continue_title.setText(name)
            self.home_continue_meta.setText(f"Page {current} of {total}" if total else f"Page {current}")
            self.home_continue_submeta.setText("Saved on this computer")
            pct = int((current / total) * 100) if total else 0
            self.home_continue_progress.setValue(max(0, min(100, pct)))
            self.home_continue_btn.setText("Continue reading")
            self._home_primary_mode = "local"
            _, _, accent = self._series_palette(name)
            self.home_hero_accent.setStyleSheet(f"background:{accent}; border:none;")
            try:
                cover_path = self.get_cover(Path(self.last_local_file))
                if cover_path:
                    local_pix = QPixmap(str(cover_path))
                    self._set_cover_pixmap(self.home_cover_art, local_pix)
            except Exception:
                pass
        else:
            self.home_cover_art.hide()
            self.home_continue_status.setText("PANELEO")
            self.home_continue_title.setText("Find something worth reading")
            self.home_continue_meta.setText("Browse BatCave or open a local CBZ, CBR or PDF.")
            self.home_continue_submeta.setText("Your reading progress will appear here automatically.")
            self.home_continue_progress.setValue(0)
            self.home_continue_btn.setText("Browse BatCave")
            self._home_primary_mode = "browse"
            self.home_hero_accent.setStyleSheet("background:#ff6b45; border:none;")

        saved_count = len(self.batcave_library.get("saved", {}))
        read_count = len(read)
        bookmark_count = len(self.batcave_library.get("bookmarks", {}))
        self.home_stat_saved.setText(str(saved_count))
        self.home_stat_read.setText(str(read_count))
        self.home_stat_bookmarks.setText(str(bookmark_count))
        self.home_stat_saved_label.setText("series")
        self.home_stat_read_label.setText("issues read")
        self.home_stat_bookmarks_label.setText("bookmarks")
        self.home_summary.setText("Pick up where you left off.")
        self.refresh_recent_list()

    def refresh_recent_list(self):
        if not hasattr(self, "home_recent_list"):
            return
        self.home_recent_list.clear()
        issues = self.batcave_library.get("issues", {})
        read = self.batcave_library.get("read", {})
        cleared_at = int(self.settings.get("recent_cleared_at", 0) or 0)
        rows = []
        for key, data in issues.items():
            if not isinstance(data, dict):
                continue
            title = self._clean_batcave_title(data.get("title", ""), data.get("url", key))
            current = int(data.get("current_page", 0) or 0)
            total = int(data.get("total_pages", 0) or 0)
            when = int(data.get("last_opened", 0) or 0)
            if when <= cleared_at:
                continue
            rows.append((when, key, data, title, current, total, key in read or canonical_url(data.get("url", key)) in read))
        rows.sort(key=lambda r: r[0], reverse=True)

        for when, key, data, title, current, total, is_read in rows[:8]:
            series_name = data.get("series") or self._series_name_from_title(title)
            issue_no = data.get("issue") or self._issue_number_from_title(title)
            ago = self._relative_time(when)
            state_text = "✓ Read" if is_read else ("In progress" if current > 1 else "Unread")
            state_kind = "read" if is_read else ("progress" if current > 1 else "unread")

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, data.get("url", key))
            item.setData(Qt.ItemDataRole.UserRole + 1, title)
            item.setData(Qt.ItemDataRole.UserRole + 2, current)
            item.setSizeHint(QSize(190, 326))

            card = QFrame()
            card.setObjectName("ComicCard")
            card.setProperty("state", state_kind)
            card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            outer = QVBoxLayout(card)
            outer.setContentsMargins(10, 10, 10, 12)
            outer.setSpacing(7)

            cover_art = QLabel()
            cover_art.setObjectName("CoverThumbnail")
            cover_art.setProperty("variant", "card")
            cover_art.setFixedSize(168, 238)
            cover_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cover_art.setScaledContents(False)
            outer.addWidget(cover_art, 0, Qt.AlignmentFlag.AlignHCenter)

            progress = QProgressBar()
            progress.setObjectName("CardProgress")
            progress.setProperty("state", state_kind)
            progress.setTextVisible(False)
            progress.setMaximum(100)
            pct = int((current / total) * 100) if current and total else (100 if is_read else 0)
            progress.setValue(max(0, min(100, pct)))
            progress.setVisible(bool(is_read or (current > 1 and total)))
            outer.addWidget(progress)

            title_label = QLabel(series_name or title)
            title_label.setTextFormat(Qt.TextFormat.PlainText)
            title_label.setWordWrap(True)
            title_label.setObjectName("ComicCardTitle")
            title_label.setMaximumHeight(46)
            outer.addWidget(title_label)

            details = []
            if issue_no:
                details.append(f"Issue #{issue_no}")
            if current and total:
                details.append(f"Page {current}/{total}")
            meta = QLabel("  •  ".join(details) if details else "Recent issue")
            meta.setObjectName("ComicCardMeta")
            outer.addWidget(meta)

            foot = QHBoxLayout()
            foot.setContentsMargins(0, 0, 0, 0)
            foot.setSpacing(6)
            state = QLabel(state_text)
            state.setObjectName("ComicCardState")
            state.setProperty("state", state_kind)
            age = QLabel(ago)
            age.setObjectName("ComicCardAge")
            age.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            foot.addWidget(state)
            foot.addStretch(1)
            foot.addWidget(age)
            outer.addLayout(foot)

            self.home_recent_list.addItem(item)
            self.home_recent_list.setItemWidget(item, card)
            self._request_series_cover(series_name, cover_art)

        self.recent_hint.setText(f"{min(len(rows), 8)} recent" if rows else "Nothing read recently")
        if hasattr(self, "clear_recent_btn"):
            self.clear_recent_btn.setEnabled(bool(rows))
    def clear_recent_history(self):
        if not hasattr(self, "home_recent_list"):
            return
        if self.home_recent_list.count() == 0:
            return
        answer = QMessageBox.question(
            self,
            "Clear Recently Read",
            "Clear the Recently Read list?\n\nYour reading progress, Reading List, read status and bookmarks will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.settings["recent_cleared_at"] = int(time.time())
        save_json(SETTINGS_FILE, self.settings)
        self.refresh_recent_list()

    def open_recent_issue(self, item):
        if not item:
            return
        url = item.data(Qt.ItemDataRole.UserRole)
        try:
            page = int(item.data(Qt.ItemDataRole.UserRole + 2) or 0)
        except Exception:
            page = 0
        if not url:
            return

        # Resume through BrowserWidget so both modern /reader/ URLs and older
        # series-page records land inside the comic at the saved page.
        self.browser.resume_issue(url, page)
        self.show_page(self.BATCAVE)

    def _relative_time(self, timestamp):
        try:
            diff = max(0, int(time.time()) - int(timestamp or 0))
        except Exception:
            return ""
        if not timestamp:
            return ""
        if diff < 60:
            return "just now"
        if diff < 3600:
            n = diff // 60
            return f"{n} min ago"
        if diff < 86400:
            n = diff // 3600
            return f"{n} hr ago"
        if diff < 604800:
            n = diff // 86400
            return f"{n} day{'s' if n != 1 else ''} ago"
        return time.strftime("%Y-%m-%d", time.localtime(int(timestamp)))

    def _bookmark_key(self, url, page):
        return f"{canonical_url(url)}|page:{int(page or 0)}"

    def toggle_current_bookmark(self, url, title, current, total):
        if not is_allowed_batcave_url(url) or current < 1 or total < 2:
            return
        bookmarks = self.batcave_library.setdefault("bookmarks", {})
        key = self._bookmark_key(url, current)
        if key in bookmarks:
            bookmarks.pop(key, None)
        else:
            clean = self._clean_batcave_title(title, url)
            bookmarks[key] = {
                "url": canonical_url(url),
                "title": clean,
                "series": self._series_name_from_title(clean),
                "issue": self._issue_number_from_title(clean),
                "page": int(current),
                "total_pages": int(total),
                "note": "",
                "added": int(time.time()),
            }
        self.save_batcave_library()
        self.refresh_bookmarks()

    def refresh_bookmarks(self, *_):
        if not hasattr(self, "bookmark_list"):
            return
        self.bookmark_list.clear()
        bookmarks = self.batcave_library.get("bookmarks", {})
        query = self.bookmark_search.text().strip().lower() if hasattr(self, "bookmark_search") else ""
        rows = []
        for key, data in bookmarks.items():
            if not isinstance(data, dict):
                continue
            title = data.get("title") or "Bookmarked issue"
            note = data.get("note") or ""
            if query and query not in title.lower() and query not in note.lower():
                continue
            rows.append((int(data.get("added", 0) or 0), key, data, title, note))
        rows.sort(key=lambda x: x[0], reverse=True)
        for added, key, data, title, note in rows:
            page = int(data.get("page", 0) or 0)
            total = int(data.get("total_pages", 0) or 0)
            label = f"🔖  {title}   •   Page {page}/{total}"
            if note:
                label += "\n     " + note
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setData(Qt.ItemDataRole.UserRole + 1, data.get("url", ""))
            item.setToolTip(note or title)
            self.bookmark_list.addItem(item)
        if not rows:
            empty = QListWidgetItem("No bookmarks yet — press 🔖 while reading an issue")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.bookmark_list.addItem(empty)
        if hasattr(self, "bookmark_stats"):
            self.bookmark_stats.setText(f"{len(bookmarks)} saved page{'s' if len(bookmarks) != 1 else ''}")

    def open_bookmark(self, item):
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        data = self.batcave_library.get("bookmarks", {}).get(key, {})
        url = data.get("url") or item.data(Qt.ItemDataRole.UserRole + 1)
        page = int(data.get("page", 0) or 0)
        if not is_allowed_batcave_url(url):
            return
        self.browser.resume_issue(url, page)
        self.show_page(self.BATCAVE)

    def edit_selected_bookmark_note(self):
        item = self.bookmark_list.currentItem() if hasattr(self, "bookmark_list") else None
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        bookmarks = self.batcave_library.get("bookmarks", {})
        data = bookmarks.get(key)
        if not isinstance(data, dict):
            return
        current = data.get("note", "")
        note, ok = QInputDialog.getText(self, "Bookmark note", "Optional note:", QLineEdit.EchoMode.Normal, current)
        if ok:
            data["note"] = note.strip()[:240]
            self.save_batcave_library()
            self.refresh_bookmarks()

    def remove_selected_bookmark(self):
        item = self.bookmark_list.currentItem() if hasattr(self, "bookmark_list") else None
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.batcave_library.setdefault("bookmarks", {}).pop(key, None)
            self.save_batcave_library()
            self.refresh_bookmarks()

    def set_sidebar_collapsed(self, collapsed, save=True):
        self.sidebar_collapsed = bool(collapsed)
        self.sidebar.setVisible(not self.sidebar_collapsed)
        self.mini_sidebar.setVisible(self.sidebar_collapsed)
        if save:
            self.settings["sidebar_collapsed"] = self.sidebar_collapsed
            save_json(SETTINGS_FILE, self.settings)

    def toggle_sidebar(self):
        self.set_sidebar_collapsed(not self.sidebar_collapsed)

    def on_batcave_reading_mode(self, on):
        if on and self.pages.currentIndex() == self.BATCAVE:
            # Only a real /reader/ URL may hide the sidebar.  BatCave/Qt can
            # briefly retain the previous issue title and Reader Mode state
            # while navigating back to the homepage; using the live URL here
            # prevents that stale event from causing a visible sidebar flash.
            if not is_batcave_reader_url(self.browser.web.url()):
                self.set_sidebar_collapsed(False, save=False)
                return
            self._sidebar_before_reading = self.sidebar_collapsed
            self.sidebar.hide()
            self.mini_sidebar.hide()
        elif not on:
            prior = getattr(self, "_sidebar_before_reading", self.sidebar_collapsed)
            self.set_sidebar_collapsed(prior, save=False)

    def handle_escape(self):
        if self.pages.currentIndex() == self.BATCAVE and self.browser.reading_mode:
            self.browser.set_reading_mode(False)
            return
        if self.isFullScreen():
            self.exit_window_fullscreen()

    def _sync_fullscreen_buttons(self, on):
        label = "Exit fullscreen" if on else "Fullscreen"
        for name in ("home_fullscreen_btn", "library_fullscreen_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setText(label)
        if hasattr(self, "reader") and hasattr(self.reader, "fullscreen_btn"):
            self.reader.fullscreen_btn.setText(label)

    def _set_reader_fullscreen_chrome(self, on):
        """Hide the app shell but retain local-reader controls in fullscreen."""
        reader_mode = bool(on and self.pages.currentIndex() == self.READER)
        self._fullscreen_reader_mode = reader_mode
        if hasattr(self, "reader"):
            self.reader.set_distraction_free(reader_mode)
        if reader_mode:
            self.sidebar.hide()
            self.mini_sidebar.hide()
        elif on:
            # Home/library/etc stay usable in app-wide fullscreen.
            self.sidebar.setVisible(self._fullscreen_sidebar_visible)
            self.mini_sidebar.setVisible(self._fullscreen_mini_sidebar_visible)

    def enter_window_fullscreen(self):
        if self.isFullScreen():
            return
        self._fullscreen_restore_maximized = self.isMaximized()
        if not self._fullscreen_restore_maximized:
            try:
                self._fullscreen_restore_geometry = self.saveGeometry()
            except Exception:
                self._fullscreen_restore_geometry = QByteArray()
        self._fullscreen_sidebar_visible = self.sidebar.isVisible()
        self._fullscreen_mini_sidebar_visible = self.mini_sidebar.isVisible()
        self._set_reader_fullscreen_chrome(True)
        self._sync_fullscreen_buttons(True)
        self.showFullScreen()

    def exit_window_fullscreen(self):
        if not self.isFullScreen():
            return
        # Leaving fullscreen must happen before restoring normal/maximized state.
        self.showNormal()
        if self._fullscreen_restore_maximized:
            self.showMaximized()
        elif not self._fullscreen_restore_geometry.isEmpty():
            try:
                self.restoreGeometry(self._fullscreen_restore_geometry)
            except Exception:
                pass
        if hasattr(self, "reader"):
            self.reader.set_distraction_free(False)
        self.set_sidebar_collapsed(self.sidebar_collapsed, save=False)
        self._fullscreen_reader_mode = False
        self._sync_fullscreen_buttons(False)

    def toggle_window_fullscreen(self):
        if self.isFullScreen():
            self.exit_window_fullscreen()
        else:
            self.enter_window_fullscreen()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose your comics folder", self.comic_dir or str(Path.home()))
        if folder:
            self.comic_dir = folder
            self.settings["comic_dir"] = folder
            save_json(SETTINGS_FILE, self.settings)
            self.scan_library()
            self.show_page(self.LIBRARY)

    def open_single_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open comic", self.comic_dir or str(Path.home()), "Comics (*.cbz *.cbr *.pdf)"
        )
        if path:
            self.open_comic(path)

    def scan_library(self):
        self.library_list.clear()
        folder_available = bool(self.comic_dir and Path(self.comic_dir).exists())
        if folder_available:
            self.folder_label.setText(self.comic_dir)
            files = [
                path for path in Path(self.comic_dir).rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED
            ]
        else:
            self.folder_label.setText("No comics folder selected")
            files = []

        # A comic opened directly is still part of the user's local reading
        # history even when no library folder has been selected. Keep the most
        # recent standalone file visible alongside any folder-scanned files.
        standalone = Path(self.last_local_file) if self.last_local_file else None
        if standalone and standalone.is_file() and standalone.suffix.lower() in SUPPORTED:
            known = {os.path.normcase(str(path.resolve())) for path in files}
            if os.path.normcase(str(standalone.resolve())) not in known:
                files.append(standalone)
            if not folder_available:
                self.folder_label.setText("Recently opened comic · no comics folder selected")

        files = sorted(files, key=natural_key)
        for path in files:
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            cover = self.get_cover(path)
            if cover:
                item.setIcon(QIcon(cover))
            self.library_list.addItem(item)
        if not files:
            empty = QListWidgetItem("No CBR, CBZ or PDF files found in this folder")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.library_list.addItem(empty)
        self.refresh_home()

    def filter_library(self, text):
        q = text.strip().lower()
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            item.setHidden(q not in item.text().lower())

    def get_cover(self, path):
        try:
            stat = path.stat()
            key = f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
            out = CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".png")
            if out.exists():
                return str(out)

            ext = path.suffix.lower()
            img = None
            if ext == ".cbz":
                with zipfile.ZipFile(path, "r") as zf:
                    infos = _zip_image_infos(zf)
                    first = infos[0]
                    if first.file_size <= MAX_IMAGE_FILE_BYTES:
                        with zf.open(first, "r") as src:
                            data = src.read(MAX_IMAGE_FILE_BYTES + 1)
                        if len(data) <= MAX_IMAGE_FILE_BYTES:
                            img = safe_qimage_from_bytes(data)
            elif ext == ".cbr":
                td = Path(tempfile.mkdtemp(prefix="comic_cover_"))
                try:
                    pics = extract_cbr_safely(path, td)
                    if pics:
                        img = safe_load_qimage(pics[0])
                finally:
                    shutil.rmtree(td, ignore_errors=True)
            elif ext == ".pdf" and fitz is not None:
                if path.stat().st_size > MAX_PDF_FILE_BYTES:
                    return None
                doc = fitz.open(str(path))
                if len(doc):
                    scale = _safe_pdf_scale(doc[0], 0.45)
                    pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    fmt = QImage.Format.Format_RGB888 if pix.n == 3 else QImage.Format.Format_RGBA8888
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
                doc.close()

            if img is not None and not img.isNull():
                pix = QPixmap.fromImage(img).scaled(
                    QSize(300, 420), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                pix.save(str(out), "PNG")
                return str(out)
        except Exception:
            return None
        return None

    def open_comic(self, path):
        self.show_page(self.READER)
        self.reader.open_file(path)
        self.reader.setFocus()

    def record_local_progress(self, path, page, total):
        self.last_local_file = path
        self.last_local_page = page
        self.last_local_total = total
        self.last_local_opened = int(time.time())
        self.settings["last_local_file"] = path
        self.settings["last_local_page"] = page
        self.settings["last_local_total"] = total
        self.settings["last_local_opened"] = self.last_local_opened
        save_json(SETTINGS_FILE, self.settings)

    def _clean_batcave_title(self, title, url=""):
        clean = (title or "").strip()
        if clean.lower().startswith("read "):
            clean = clean[5:]
        pos = clean.lower().find(" comics online")
        if pos >= 0:
            clean = clean[:pos]
        if not clean:
            clean = url.replace("https://", "").replace("http://", "")
        return clean.strip() or "BatCave item"

    def _series_name_from_title(self, title):
        """Return a stable series name from a BatCave issue title."""
        import re
        clean = self._clean_batcave_title(title or "")
        # BatCave issue titles normally end in " #1", " #12", etc.
        clean = re.sub(r"\s+#(?:\d+(?:\.\d+)?|[A-Za-z0-9._-]+)\s*$", "", clean).strip()
        return clean or self._clean_batcave_title(title or "")

    def _issue_number_from_title(self, title):
        import re
        clean = self._clean_batcave_title(title or "")
        m = re.search(r"#([^#\s]+)\s*$", clean)
        return m.group(1) if m else ""

    def _issue_identity(self, data, fallback_key=""):
        """Stable identity used to prevent one BatCave issue appearing twice."""
        if not isinstance(data, dict):
            return ("url", canonical_url(fallback_key))
        title = self._clean_batcave_title(data.get("title", ""), data.get("url", fallback_key))
        series = (data.get("series") or self._series_name_from_title(title) or "").strip().lower()
        issue = str(data.get("issue") or self._issue_number_from_title(title) or "").strip().lower()
        if series and issue:
            return ("issue", series, issue)
        return ("url", canonical_url(data.get("url", fallback_key)))

    def _dedupe_issue_store(self):
        """Merge legacy/variant URL records for the same issue, keeping the newest progress."""
        issues = self.batcave_library.setdefault("issues", {})
        if not isinstance(issues, dict) or len(issues) < 2:
            return False
        winners = {}
        aliases = {}
        changed = False
        for key, raw in list(issues.items()):
            if not isinstance(raw, dict):
                continue
            data = dict(raw)
            ident = self._issue_identity(data, key)
            aliases.setdefault(ident, []).append(key)
            old = winners.get(ident)
            if old is None:
                winners[ident] = (key, data)
                continue
            old_key, old_data = old
            old_time = int(old_data.get("last_opened", 0) or 0)
            new_time = int(data.get("last_opened", 0) or 0)
            old_page = int(old_data.get("current_page", 0) or 0)
            new_page = int(data.get("current_page", 0) or 0)
            if (new_time, new_page) >= (old_time, old_page):
                winners[ident] = (key, data)
            changed = True
        if not changed:
            return False

        read = self.batcave_library.setdefault("read", {})
        rebuilt = {}
        for ident, (winner_key, winner_data) in winners.items():
            canonical_key = canonical_url(winner_data.get("url", winner_key)) or winner_key
            winner_data["url"] = winner_data.get("url", winner_key)
            rebuilt[canonical_key] = winner_data
            # Preserve Read state if any duplicate alias had been marked read.
            alias_keys = aliases.get(ident, [])
            read_rows = [read.get(k) for k in alias_keys if isinstance(read.get(k), dict)]
            if read_rows and canonical_key not in read:
                read[canonical_key] = max(read_rows, key=lambda d: int(d.get("read_at", 0) or 0))
            for alias in alias_keys:
                if alias != canonical_key:
                    read.pop(alias, None)
        self.batcave_library["issues"] = rebuilt
        save_json(BATCAVE_LIBRARY_FILE, self.batcave_library)
        return True

    def record_batcave_issue_progress(self, url, title, current=0, total=0):
        """Persist the latest page for every BatCave issue the user opens."""
        if not is_allowed_batcave_url(url):
            return
        try:
            current = int(current or 0)
            total = int(total or 0)
        except Exception:
            return
        if current < 1 or total < 2:
            return
        key = canonical_url(url)
        issues = self.batcave_library.setdefault("issues", {})
        clean_title = self._clean_batcave_title(title, url)
        incoming_identity = self._issue_identity({
            "url": url,
            "title": clean_title,
            "series": self._series_name_from_title(clean_title),
            "issue": self._issue_number_from_title(clean_title),
        }, key)
        # BatCave can expose slightly different reader URLs for the same issue.
        # Remove any older alias so one issue can never become two list rows.
        for existing_key, existing_data in list(issues.items()):
            if existing_key != key and self._issue_identity(existing_data, existing_key) == incoming_identity:
                issues.pop(existing_key, None)
        old = issues.get(key, {}) if isinstance(issues.get(key, {}), dict) else {}
        cleared_at = int(self.settings.get("recent_cleared_at", 0) or 0)
        opened_at = max(int(time.time()), cleared_at + 1 if cleared_at else 0)
        series_name = self._series_name_from_title(clean_title)
        cover_key = (series_name or "").strip().lower()
        data = {
            "url": url,
            "title": clean_title,
            "series": series_name,
            "issue": self._issue_number_from_title(clean_title),
            "current_page": current,
            "total_pages": total,
            "last_opened": opened_at,
        }
        cover_page = self._cover_page_cache.get(cover_key, "")
        cover_image = self._cover_live_urls.get(cover_key, "")
        if is_allowed_batcave_url(cover_page):
            data["cover_page_url"] = canonical_url(cover_page)
        elif is_allowed_batcave_url(old.get("cover_page_url", "")):
            data["cover_page_url"] = old.get("cover_page_url")
        if is_allowed_batcave_asset_url(cover_image):
            data["cover_image_url"] = QUrl(str(cover_image)).toString()
        elif is_allowed_batcave_asset_url(old.get("cover_image_url", "")):
            data["cover_image_url"] = old.get("cover_image_url")
        # Avoid hammering the JSON file if nothing visible changed. If Recent
        # was just cleared, allow one write so reopening this issue makes it
        # visible in Recently Read again even when the page did not change.
        old_opened = int(old.get("last_opened", 0) or 0)
        if (old.get("current_page") == current and old.get("total_pages") == total
                and old.get("title") == clean_title and old_opened > cleared_at
                and old.get("cover_page_url", "") == data.get("cover_page_url", "")
                and old.get("cover_image_url", "") == data.get("cover_image_url", "")):
            return
        issues[key] = data
        self.save_batcave_library()

    def sync_browser_tracking(self):
        if hasattr(self, "browser"):
            self.browser.set_tracking_data(self.batcave_library.get("saved", {}).keys(), self.batcave_library.get("read", {}).keys())

    def save_batcave_library(self):
        save_json(BATCAVE_LIBRARY_FILE, self.batcave_library)
        self.sync_browser_tracking()
        if hasattr(self, "reading_list"):
            self.refresh_reading_list()
        if hasattr(self, "home_stat_saved"):
            self.refresh_home()
        if hasattr(self, "bookmark_list"):
            self.refresh_bookmarks()
        if hasattr(self, "series_issue_list") and self.pages.currentIndex() == self.SERIES_DETAILS:
            self.refresh_series_details()

    def toggle_saved_batcave(self, url, title):
        if not is_allowed_batcave_url(url):
            return
        key = canonical_url(url)
        saved = self.batcave_library.setdefault("saved", {})
        if key in saved:
            saved.pop(key, None)
        else:
            clean_title = self._clean_batcave_title(title, url)
            series_name = self._series_name_from_title(clean_title)
            cover_key = (series_name or "").strip().lower()
            row = {"url": url, "title": clean_title, "added": int(time.time())}
            cover_page = self._cover_page_cache.get(cover_key, "")
            cover_image = self._cover_live_urls.get(cover_key, "")
            if is_allowed_batcave_url(cover_page):
                row["cover_page_url"] = canonical_url(cover_page)
            if is_allowed_batcave_asset_url(cover_image):
                row["cover_image_url"] = QUrl(str(cover_image)).toString()
            saved[key] = row
        self.save_batcave_library()

    def auto_mark_read_batcave(self, url, title, current=0, total=0):
        """Mark an issue read when BatCave reports that its last page was reached.

        This is intentionally one-way: automatic tracking never changes an
        already-read issue back to Unread. Manual Mark Unread still works.
        """
        if not is_allowed_batcave_url(url):
            return
        key = canonical_url(url)
        read = self.batcave_library.setdefault("read", {})
        if key in read:
            return
        read[key] = {
            "url": url,
            "title": self._clean_batcave_title(title, url),
            "read_at": int(time.time()),
            "automatic": True,
            "completed_page": int(current or 0),
            "total_pages": int(total or 0),
        }
        self.save_batcave_library()

    def toggle_read_batcave(self, url, title):
        if not is_allowed_batcave_url(url):
            return
        key = canonical_url(url)
        read = self.batcave_library.setdefault("read", {})
        if key in read:
            read.pop(key, None)
        else:
            read[key] = {"url": url, "title": self._clean_batcave_title(title, url), "read_at": int(time.time())}
        self.save_batcave_library()

    def refresh_reading_list(self, *_):
        if not hasattr(self, "reading_list"):
            return
        self.reading_list.clear()
        # One-time cleanup for duplicate issue rows left by older URL variants.
        self._dedupe_issue_store()
        saved = self.batcave_library.get("saved", {})
        read = self.batcave_library.get("read", {})
        issues = self.batcave_library.get("issues", {})
        query = self.reading_search.text().strip().lower() if hasattr(self, "reading_search") else ""
        filt = self.reading_filter.currentText() if hasattr(self, "reading_filter") else "All"

        merged_issues = {}
        for key, data in issues.items():
            if isinstance(data, dict):
                merged_issues[key] = dict(data)
        for key, data in read.items():
            if not isinstance(data, dict):
                continue
            row = merged_issues.setdefault(key, {})
            row.setdefault("url", data.get("url", key))
            row.setdefault("title", data.get("title", ""))
            row.setdefault("series", self._series_name_from_title(row.get("title", "")))
            row.setdefault("issue", self._issue_number_from_title(row.get("title", "")))
            row.setdefault("current_page", int(data.get("completed_page", 0) or 0))
            row.setdefault("total_pages", int(data.get("total_pages", 0) or 0))
            row.setdefault("last_opened", int(data.get("read_at", 0) or 0))

        saved_items = list(saved.items())

        def saved_series_metrics(kv):
            series_key, data = kv
            series_title = self._clean_batcave_title(data.get("title", ""), data.get("url", series_key))
            series_name = self._series_name_from_title(series_title)
            rows = []
            for key, issue_data in merged_issues.items():
                title = self._clean_batcave_title(issue_data.get("title", ""), issue_data.get("url", key))
                issue_series = issue_data.get("series") or self._series_name_from_title(title)
                if issue_series.lower() != series_name.lower():
                    continue
                current = int(issue_data.get("current_page", 0) or 0)
                total = int(issue_data.get("total_pages", 0) or 0)
                last_opened = int(issue_data.get("last_opened", 0) or 0)
                if key in read:
                    fractional = 1.0
                elif total > 0:
                    fractional = max(0.0, min(1.0, current / total))
                else:
                    fractional = 0.0
                rows.append((fractional, last_opened))
            progress = (sum(r[0] for r in rows) / len(rows)) if rows else 0.0
            recent = max((r[1] for r in rows), default=0)
            return series_name.lower(), progress, recent

        if self.reading_sort_mode == "Title A–Z":
            saved_items.sort(key=lambda kv: saved_series_metrics(kv)[0])
        elif self.reading_sort_mode == "Progress":
            saved_items.sort(key=lambda kv: (-saved_series_metrics(kv)[1], saved_series_metrics(kv)[0]))
        else:
            saved_items.sort(key=lambda kv: (-saved_series_metrics(kv)[2], saved_series_metrics(kv)[0]))

        shown = 0
        tracked_keys = set()
        force_expand = bool(query) or filt != "All"

        for series_key, data in saved_items:
            series_title = self._clean_batcave_title(data.get("title", ""), data.get("url", series_key))
            series_name = self._series_name_from_title(series_title)

            children = []
            for key, issue_data in merged_issues.items():
                title = self._clean_batcave_title(issue_data.get("title", ""), issue_data.get("url", key))
                issue_series = issue_data.get("series") or self._series_name_from_title(title)
                if issue_series.lower() != series_name.lower():
                    continue
                is_read = key in read
                current = int(issue_data.get("current_page", 0) or 0)
                total = int(issue_data.get("total_pages", 0) or 0)
                issue_no = issue_data.get("issue") or self._issue_number_from_title(title)
                children.append((key, issue_data, title, issue_no, is_read, current, total))
                tracked_keys.add(key)

            def issue_sort(row):
                raw = str(row[3] or "")
                try:
                    return (0, float(raw))
                except Exception:
                    return (1, raw.lower())
            children.sort(key=issue_sort)

            matching_children = []
            for row in children:
                key, issue_data, title, issue_no, is_read, current, total = row
                in_progress = (not is_read and current > 1)
                if filt == "Unread" and is_read:
                    continue
                if filt == "In Progress" and not in_progress:
                    continue
                if filt == "Read" and not is_read:
                    continue
                if query and query not in title.lower() and query not in series_name.lower():
                    continue
                matching_children.append(row)

            series_matches = (not query or query in series_name.lower() or query in series_title.lower())
            if filt == "All":
                include_series = series_matches or bool(matching_children)
            else:
                include_series = bool(matching_children)
            if not include_series:
                continue

            read_count = sum(1 for r in children if r[4])
            in_progress_count = sum(1 for r in children if (not r[4] and r[5] > 1))
            if children and read_count == len(children):
                accent_state = "read"
            elif in_progress_count:
                accent_state = "progress"
            else:
                accent_state = "unread"
            suffix_parts = []
            if children:
                suffix_parts.append(f"{read_count}/{len(children)} read")
                if in_progress_count:
                    suffix_parts.append(f"{in_progress_count} in progress")
            else:
                suffix_parts.append("No issues opened yet")

            expanded = bool(self.reading_list_expanded.get(series_key, in_progress_count > 0))
            if force_expand:
                expanded = True

            header_item = QListWidgetItem()
            header_item.setData(Qt.ItemDataRole.UserRole, data.get("url", series_key))
            header_item.setData(Qt.ItemDataRole.UserRole + 1, series_name)
            header_item.setData(Qt.ItemDataRole.UserRole + 2, "series")
            header_item.setData(Qt.ItemDataRole.UserRole + 3, series_key)
            # Remember whether this saved series has tracked issues. A single
            # click on an empty series should open its BatCave
            # page instead of toggling a dropdown that has nothing to show.
            header_item.setData(Qt.ItemDataRole.UserRole + 4, len(children))
            header_item.setToolTip(data.get("url", series_key))
            header_widget = self._build_reading_series_widget(series_name, " • ".join(suffix_parts), expanded, accent_state, read_count, len(children))
            header_item.setSizeHint(header_widget.sizeHint())
            self.reading_list.addItem(header_item)
            self.reading_list.setItemWidget(header_item, header_widget)
            shown += 1

            if expanded:
                for key, issue_data, title, issue_no, is_read, current, total in matching_children:
                    if is_read:
                        state_text, state_kind = "READ", "read"
                    elif current > 1:
                        state_text, state_kind = "IN PROGRESS", "progress"
                    else:
                        state_text, state_kind = "UNREAD", "unread"
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, issue_data.get("url", key))
                    item.setData(Qt.ItemDataRole.UserRole + 1, title)
                    item.setData(Qt.ItemDataRole.UserRole + 2, "issue")
                    item.setToolTip(title + (f" — Page {current}/{total}" if current and total else ""))
                    widget = self._build_reading_issue_widget(title, issue_no, state_text, state_kind, current, total)
                    item.setSizeHint(widget.sizeHint())
                    self.reading_list.addItem(item)
                    self.reading_list.setItemWidget(item, widget)
                    shown += 1

        leftovers = [(k, d) for k, d in merged_issues.items() if k not in tracked_keys]
        if leftovers:
            orphan_rows = []
            for key, issue_data in leftovers:
                title = self._clean_batcave_title(issue_data.get("title", ""), issue_data.get("url", key))
                is_read = key in read
                current = int(issue_data.get("current_page", 0) or 0)
                total = int(issue_data.get("total_pages", 0) or 0)
                in_progress = (not is_read and current > 1)
                if filt == "Unread" and is_read:
                    continue
                if filt == "In Progress" and not in_progress:
                    continue
                if filt == "Read" and not is_read:
                    continue
                if query and query not in title.lower():
                    continue
                orphan_rows.append((key, issue_data, title, is_read, current, total))
            if orphan_rows:
                sep = QListWidgetItem("Other tracked issues")
                sep.setFlags(Qt.ItemFlag.NoItemFlags)
                self.reading_list.addItem(sep)
                shown += 1
                for key, issue_data, title, is_read, current, total in orphan_rows:
                    state_text = "READ" if is_read else ("IN PROGRESS" if current > 1 else "UNREAD")
                    state_kind = "read" if is_read else ("progress" if current > 1 else "unread")
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, issue_data.get("url", key))
                    item.setData(Qt.ItemDataRole.UserRole + 1, title)
                    item.setData(Qt.ItemDataRole.UserRole + 2, "issue")
                    widget = self._build_reading_issue_widget(title, issue_data.get("issue") or self._issue_number_from_title(title), state_text, state_kind, current, total)
                    item.setSizeHint(widget.sizeHint())
                    self.reading_list.addItem(item)
                    self.reading_list.setItemWidget(item, widget)
                    shown += 1

        if not shown:
            empty = QListWidgetItem("No saved comics match this view" if saved else "Nothing saved yet — use ☆ Save while browsing BatCave")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.reading_list.addItem(empty)

        if hasattr(self, "reading_stats"):
            issue_count = len(merged_issues)
            read_count = sum(1 for k in merged_issues if k in read)
            in_progress = sum(1 for k, d in merged_issues.items() if k not in read and int(d.get("current_page", 0) or 0) > 1)
            self.reading_stats.setText(f"{len(saved)} series • {read_count}/{issue_count} issues read • {in_progress} in progress")

    def _persist_reading_list_state(self):
        self.settings["reading_list_expanded"] = dict(self.reading_list_expanded)
        save_json(SETTINGS_FILE, self.settings)

    def expand_all_series(self):
        for key in self.batcave_library.get("saved", {}).keys():
            self.reading_list_expanded[key] = True
        self._persist_reading_list_state()
        self.refresh_reading_list()

    def collapse_all_series(self):
        for key in self.batcave_library.get("saved", {}).keys():
            self.reading_list_expanded[key] = False
        self._persist_reading_list_state()
        self.refresh_reading_list()

    def on_reading_item_clicked(self, item):
        if not item or item.data(Qt.ItemDataRole.UserRole + 2) != "series":
            return

        # A saved series with no tracked/opened issues has no useful dropdown
        # content. Open its BatCave series page on a normal
        # click instead. Series that already have issue history keep the
        # existing click-to-expand/collapse behavior.
        try:
            tracked_count = int(item.data(Qt.ItemDataRole.UserRole + 4) or 0)
        except Exception:
            tracked_count = 0
        if tracked_count <= 0:
            url = item.data(Qt.ItemDataRole.UserRole)
            if url:
                self.browser.resume(url)
                self.show_page(self.BATCAVE)
            return

        series_key = item.data(Qt.ItemDataRole.UserRole + 3)
        if not series_key:
            series_key = canonical_url(item.data(Qt.ItemDataRole.UserRole) or "")
        if not series_key:
            return
        self.reading_list_expanded[series_key] = not bool(self.reading_list_expanded.get(series_key, False))
        self._persist_reading_list_state()
        self.refresh_reading_list()

    def _build_reading_series_widget(self, series_name, meta_text, expanded, accent_state, read_count=0, total_count=0):
        frame = QFrame()
        frame.setObjectName("ReadingListRow")
        frame.setProperty("expanded", "true" if expanded else "false")
        frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 16, 14, 16)
        lay.setSpacing(12)

        accent = QFrame()
        accent.setObjectName("seriesAccent")
        accent.setFixedWidth(4)
        _, _, accent_color = self._series_palette(series_name)
        accent.setStyleSheet(f"background:{accent_color}; border:none; border-radius:2px;")
        lay.addWidget(accent)

        chevron = QLabel("▾" if expanded else "›")
        chevron.setObjectName("seriesChevron")
        chevron.setFixedWidth(18)
        lay.addWidget(chevron)

        title_wrap = QWidget()
        title_wrap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        tw = QVBoxLayout(title_wrap)
        tw.setContentsMargins(0, 0, 0, 0)
        tw.setSpacing(3)
        title = QLabel(series_name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("readingSeriesTitle")
        meta = QLabel(meta_text)
        meta.setObjectName("readingMeta")
        tw.addWidget(title)
        tw.addWidget(meta)
        if total_count and 0 < int(read_count or 0) < int(total_count or 0):
            bar = QProgressBar()
            bar.setObjectName("seriesRowProgress")
            bar.setTextVisible(False)
            bar.setFixedWidth(240)
            bar.setMaximum(max(1, int(total_count or 0)))
            bar.setValue(max(0, min(int(read_count or 0), max(1, int(total_count or 0)))))
            tw.addWidget(bar)
        lay.addWidget(title_wrap, 1)

        if accent_state != "unread":
            state_label = QLabel("In progress" if accent_state == "progress" else "✓ Read")
            state_label.setObjectName("seriesState")
            state_label.setProperty("state", accent_state)
            state_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            state_label.setFixedWidth(104)
            lay.addWidget(state_label)
        return frame

    def _build_reading_issue_widget(self, title, issue_no, state_text, state_kind, current, total):
        frame = QFrame()
        frame.setObjectName("IssueRow")
        frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(74, 13, 14, 13)
        lay.setSpacing(12)

        issue_number = QLabel(f"#{issue_no or '?'}")
        issue_number.setObjectName("readingIssueNumber")
        issue_number.setFixedWidth(52)
        lay.addWidget(issue_number)

        title_wrap = QWidget()
        title_wrap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        tv = QVBoxLayout(title_wrap)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(2)
        title_label = QLabel(title or "Issue")
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        title_label.setObjectName("readingIssueTitle")
        title_label.setWordWrap(False)
        meta = QLabel(f"Page {current}/{total}" if current and total else "Not opened")
        meta.setObjectName("readingMeta")
        tv.addWidget(title_label)
        tv.addWidget(meta)
        lay.addWidget(title_wrap, 1)

        state = QLabel("✓ Read" if state_kind == "read" else ("In progress" if state_kind == "progress" else "Unread"))
        state.setObjectName("issueState")
        state.setProperty("state", state_kind)
        state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        state.setFixedWidth(92)
        lay.addWidget(state)
        return frame

    def open_saved_item(self, item):
        if not item:
            return
        url = item.data(Qt.ItemDataRole.UserRole)
        kind = item.data(Qt.ItemDataRole.UserRole + 2)
        if kind == "series":
            self.current_series_key = item.data(Qt.ItemDataRole.UserRole + 3) or canonical_url(url or "")
            self.current_series_url = url or ""
            self.current_series_name = item.data(Qt.ItemDataRole.UserRole + 1) or "Series"
            self.show_page(self.SERIES_DETAILS)
            return
        if url:
            self.browser.resume(url)
            self.show_page(self.BATCAVE)

    def open_selected_saved(self):
        self.open_saved_item(self.reading_list.currentItem())

    def toggle_selected_read(self):
        item = self.reading_list.currentItem()
        if not item:
            return
        kind = item.data(Qt.ItemDataRole.UserRole + 2)
        url = item.data(Qt.ItemDataRole.UserRole)
        title = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
        if url and kind == "issue":
            self.toggle_read_batcave(url, title)

    def remove_selected_saved(self):
        item = self.reading_list.currentItem()
        if not item:
            return
        kind = item.data(Qt.ItemDataRole.UserRole + 2)
        url = item.data(Qt.ItemDataRole.UserRole)
        if not url:
            return
        key = canonical_url(url)
        if kind == "series":
            self.batcave_library.setdefault("saved", {}).pop(key, None)
        elif kind == "issue":
            # Remove only this issue's tracking history; do not remove its series.
            self.batcave_library.setdefault("issues", {}).pop(key, None)
            self.batcave_library.setdefault("read", {}).pop(key, None)
        self.save_batcave_library()

    def refresh_series_details(self):
        if not hasattr(self, "series_issue_list"):
            return
        self.series_issue_list.clear()
        series_name = self.current_series_name or "Series"
        self.series_title.setText(series_name)
        issues = self.batcave_library.get("issues", {})
        read = self.batcave_library.get("read", {})
        cleared_at = int(self.settings.get("recent_cleared_at", 0) or 0)
        rows = []
        for key, data in issues.items():
            if not isinstance(data, dict):
                continue
            title = self._clean_batcave_title(data.get("title", ""), data.get("url", key))
            issue_series = data.get("series") or self._series_name_from_title(title)
            if issue_series.lower() != series_name.lower():
                continue
            issue_no = data.get("issue") or self._issue_number_from_title(title)
            current = int(data.get("current_page", 0) or 0)
            total = int(data.get("total_pages", 0) or 0)
            rows.append((issue_no, key, data, title, current, total, key in read))
        def sort_key(row):
            try: return (0, float(str(row[0] or "")))
            except Exception: return (1, str(row[0] or "").lower())
        rows.sort(key=sort_key)
        read_count = sum(1 for r in rows if r[6])
        in_progress = sum(1 for r in rows if not r[6] and r[4] > 1)
        self.series_summary.setText(f"{len(rows)} tracked issue{'s' if len(rows) != 1 else ''} • {read_count} read • {in_progress} in progress")
        self.series_progress.setMaximum(max(1, len(rows)))
        self.series_progress.setValue(read_count)
        self.series_progress.setFormat(f"{read_count}/{len(rows)} issues read" if rows else "No tracked issues yet")
        for issue_no, key, data, title, current, total, is_read in rows:
            state_text = "READ" if is_read else ("IN PROGRESS" if current > 1 else "UNREAD")
            state_kind = "read" if is_read else ("progress" if current > 1 else "unread")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, data.get("url", key))
            item.setData(Qt.ItemDataRole.UserRole + 1, title)
            item.setData(Qt.ItemDataRole.UserRole + 2, "issue")
            widget = self._build_reading_issue_widget(title, issue_no, state_text, state_kind, current, total)
            item.setSizeHint(widget.sizeHint())
            self.series_issue_list.addItem(item)
            self.series_issue_list.setItemWidget(item, widget)
        if not rows:
            empty = QListWidgetItem("No issues tracked for this series yet. Open an issue on BatCave and it will appear here.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.series_issue_list.addItem(empty)

    def open_current_series_web(self):
        if self.current_series_url:
            self.browser.resume(self.current_series_url)
            self.show_page(self.BATCAVE)

    def open_series_issue(self, item):
        url = item.data(Qt.ItemDataRole.UserRole) if item else None
        if url:
            self.browser.resume(url)
            self.show_page(self.BATCAVE)

    def open_selected_series_issue(self):
        self.open_series_issue(self.series_issue_list.currentItem())

    def toggle_selected_series_issue_read(self):
        item = self.series_issue_list.currentItem()
        if not item or item.data(Qt.ItemDataRole.UserRole + 2) != "issue":
            return
        url = item.data(Qt.ItemDataRole.UserRole)
        title = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        if url:
            self.toggle_read_batcave(url, title)
            self.refresh_series_details()

    def _backup_payload(self):
        return {
            "format": "PaneleoBackup",
            "version": 1,
            "app_version": APP_VERSION,
            "created_at": int(time.time()),
            "settings": self.settings,
            "progress": self.progress_store,
            "batcave_library": self.batcave_library,
        }

    def backup_now(self):
        try:
            folder = app_data_dir() / "backups"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = folder / f"paneleo-backup-{stamp}.json"
            path.write_text(json.dumps(self._backup_payload(), indent=2), encoding="utf-8")
            QMessageBox.information(self, "Backup created", f"Backup saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", str(e))

    def export_reader_data(self):
        default = str(Path.home() / f"Paneleo-backup-{time.strftime('%Y-%m-%d')}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export Paneleo data", default, "JSON files (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            Path(path).write_text(json.dumps(self._backup_payload(), indent=2), encoding="utf-8")
            QMessageBox.information(self, "Export complete", "Your reading data was exported successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def import_reader_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Paneleo data", str(Path.home()), "JSON files (*.json)")
        if not path:
            return
        try:
            import_path = Path(path)
            if not import_path.is_file() or import_path.stat().st_size > MAX_BACKUP_FILE_BYTES:
                raise ValueError("Backup file is too large or invalid.")
            payload = json.loads(import_path.read_text(encoding="utf-8"))
            settings, progress, library = validate_backup_payload(payload)
            answer = QMessageBox.question(self, "Import backup", "Replace your current settings and reading history with this validated backup?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.settings = settings
            self.progress_store.clear(); self.progress_store.update(progress)
            self.batcave_library = library
            self.batcave_library.setdefault("saved", {})
            self.batcave_library.setdefault("read", {})
            self.batcave_library.setdefault("issues", {})
            self.batcave_library.setdefault("bookmarks", {})
            self.reading_list_expanded = self.settings.get("reading_list_expanded", {}) if isinstance(self.settings.get("reading_list_expanded", {}), dict) else {}
            self.series_zoom = self.settings.get("series_zoom", {}) if isinstance(self.settings.get("series_zoom", {}), dict) else {}
            self.reading_sort_mode = self.settings.get("reading_sort_mode", "Recently read")
            if self.reading_sort_mode not in ("Recently read", "Title A–Z", "Progress"):
                self.reading_sort_mode = "Recently read"
            if hasattr(self, "reading_sort"):
                idx = self.reading_sort.findText(self.reading_sort_mode)
                self.reading_sort.blockSignals(True)
                self.reading_sort.setCurrentIndex(max(0, idx))
                self.reading_sort.blockSignals(False)
            save_json(SETTINGS_FILE, self.settings)
            save_json(PROGRESS_FILE, self.progress_store)
            save_json(BATCAVE_LIBRARY_FILE, self.batcave_library)
            self._cover_page_cache.clear()
            self._cover_live_urls.clear()
            self._cover_failed.clear()
            self._prime_cover_metadata_from_library()
            self.sync_browser_tracking()
            self.refresh_reading_list()
            self.refresh_home()
            QMessageBox.information(self, "Import complete", "Backup imported successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))

    def _series_zoom_key(self, title):
        name = self._series_name_from_title(title or "").strip().lower()
        return name

    def record_batcave_zoom(self, url, title, factor):
        if not is_allowed_batcave_url(url):
            return
        key = self._series_zoom_key(title)
        if not key:
            return
        try:
            factor = max(0.50, min(2.50, round(float(factor), 2)))
        except Exception:
            return
        self.series_zoom[key] = factor
        self.settings["series_zoom"] = dict(self.series_zoom)
        save_json(SETTINGS_FILE, self.settings)

    def restore_batcave_zoom(self, title, expected_url="", attempt=0):
        """Restore saved zoom only after BatCave's active page image is ready.

        Applying image sizing while BatCave is still lazy-loading/swapping the
        reader image can produce a black frame on some QtWebEngine/GPU setups.
        Wait for the active image to be complete and decoded before touching it.
        """
        if expected_url and canonical_url(self.browser.web.url().toString()) != canonical_url(expected_url):
            return
        key = self._series_zoom_key(title)
        factor = self.series_zoom.get(key, 1.0) if key else 1.0
        try:
            factor = float(factor)
        except Exception:
            factor = 1.0

        # A fresh issue at 100% needs no image-DOM mutation at all.
        if abs(factor - 1.0) < 0.001:
            self.browser.zoom_factor = 1.0
            self.browser.web.setZoomFactor(1.0)
            self.browser.zoom_reset.setText("100%")
            return

        readiness_js = r"""
(() => {
  const active = document.querySelector('.reader__item-wrap.active img.reader__item')
      || document.querySelector('.reader-root .reader__item-wrap.active img')
      || document.querySelector('.reader-root img.reader__item');
  if (!active) return JSON.stringify({ready:false});
  const r=active.getBoundingClientRect();
  return JSON.stringify({
    ready: !!active.complete && (active.naturalWidth||0)>100 && (active.naturalHeight||0)>100 && r.width>100 && r.height>100
  });
})();
"""
        def checked(result, t=title, u=expected_url, f=factor, n=attempt):
            if u and canonical_url(self.browser.web.url().toString()) != canonical_url(u):
                return
            ready = False
            if isinstance(result, str):
                try:
                    ready = bool(json.loads(result).get("ready", False))
                except Exception:
                    ready = False
            if ready:
                # Give BatCave a moment to finish its own post-load class/style update.
                QTimer.singleShot(180, lambda: self._apply_ready_series_zoom(t, u, f))
            elif n < 16:
                QTimer.singleShot(250, lambda: self.restore_batcave_zoom(t, u, n + 1))
        self.browser.web.page().runJavaScript(readiness_js, checked)

    def _apply_ready_series_zoom(self, title, expected_url, factor):
        if expected_url and canonical_url(self.browser.web.url().toString()) != canonical_url(expected_url):
            return
        self.browser.set_zoom_factor(factor, emit=False)

    def on_reading_sort_changed(self, mode):
        if mode not in ("Recently read", "Title A–Z", "Progress"):
            mode = "Recently read"
        self.reading_sort_mode = mode
        self.settings["reading_sort_mode"] = mode
        save_json(SETTINGS_FILE, self.settings)
        self.refresh_reading_list()

    def record_batcave_fit_mode(self, mode):
        self.batcave_fit_mode = mode
        self.settings["batcave_fit_mode"] = mode
        save_json(SETTINGS_FILE, self.settings)

    def record_batcave_url(self, url):
        # Save only BatCave URLs so external redirects do not replace the resume point.
        if is_allowed_batcave_url(url):
            self.last_batcave_url = url
            self.settings["last_batcave_url"] = url
            save_json(SETTINGS_FILE, self.settings)

    def record_batcave_issue(self, url, title):
        if not is_allowed_batcave_url(url):
            return
        self.last_batcave_issue_url = url
        self.last_batcave_title = title or ""
        self.settings["last_batcave_issue_url"] = url
        self.settings["last_batcave_title"] = self.last_batcave_title
        save_json(SETTINGS_FILE, self.settings)
        # Restore the preferred zoom for this series after BatCave has created
        # the new issue document. This also resets to 100% when switching to a
        # series that has no custom zoom.
        QTimer.singleShot(350, lambda t=title, u=url: self.restore_batcave_zoom(t, u))
        self.refresh_home()

    def resume_local(self):
        if self.last_local_file and Path(self.last_local_file).exists():
            self.open_comic(self.last_local_file)
        else:
            self.show_page(self.LIBRARY)

    def open_batcave_home(self):
        """Open the BatCave main page from the sidebar.

        Browsing BatCave should keep the normal sidebar visible.  The sidebar
        is hidden only when an actual issue enters Reader Mode.
        """
        if self.browser.reading_mode:
            self.browser.set_reading_mode(False)

        # Reader Mode remembers the sidebar state that existed before reading.
        # When Home is requested explicitly, reset that remembered state and
        # force the full sidebar visible so a previous reading session cannot
        # leave BatCave browsing in the collapsed/hidden state.
        self._sidebar_before_reading = False
        self.set_sidebar_collapsed(False, save=False)

        self.browser.resume(BATCAVE_URL)
        self.show_page(self.BATCAVE)

    def resume_batcave(self):
        self.browser.resume(self.last_batcave_issue_url or self.last_batcave_url or BATCAVE_URL)
        self.show_page(self.BATCAVE)

    def closeEvent(self, event):
        self.reader.cleanup()
        if self.isFullScreen():
            self.settings["window_maximized"] = bool(self._fullscreen_restore_maximized)
            if (not self._fullscreen_restore_maximized
                    and not self._fullscreen_restore_geometry.isEmpty()):
                try:
                    self.settings["window_geometry"] = bytes(
                        self._fullscreen_restore_geometry.toHex()
                    ).decode("ascii")
                except Exception:
                    pass
        else:
            self.settings["window_maximized"] = bool(self.isMaximized())
            try:
                self.settings["window_geometry"] = bytes(self.saveGeometry().toHex()).decode("ascii")
            except Exception:
                pass
        save_json(SETTINGS_FILE, self.settings)
        save_json(PROGRESS_FILE, self.progress_store)
        save_json(BATCAVE_LIBRARY_FILE, self.batcave_library)
        super().closeEvent(event)


def main():
    # Keep QtWebEngine/Chromium's normal sandbox/integrity protections enabled.
    # Paneleo does not require custom Chromium flags or remote debugging.
    os.environ.pop("QTWEBENGINE_DISABLE_SANDBOX", None)
    os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    os.environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    if bool(window.settings.get("window_maximized", False)):
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
