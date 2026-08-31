import os
import sys
import runpy
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "crash_log.txt"
os.chdir(ROOT)

# A previous crash log should not make a healthy run look broken.
try:
    if LOG.exists():
        LOG.unlink()
except Exception:
    pass

def report_crash(details):
    try:
        LOG.write_text(details, encoding="utf-8")
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Paneleo could not start.\n\nA crash log was saved to:\n" + str(LOG),
                "Paneleo startup error",
                0x10,
            )
        except Exception:
            pass

try:
    runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
except SystemExit as exc:
    # QApplication exits by raising SystemExit(app.exec()). Exit code 0 is a
    # completely normal shutdown and must never be reported as a crash.
    code = exc.code
    if code not in (None, 0):
        report_crash(traceback.format_exc())
        raise
except Exception:
    report_crash(traceback.format_exc())
    raise
