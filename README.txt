Paneleo 2.0.0-beta8.4 — Fullscreen & Local Reader Polish

Changes in this build:
- Fullscreen is now app-wide and uses one shared fullscreen system.
- Press F11 anywhere in Paneleo to enter/exit fullscreen.
- Home and Local Library now have visible Fullscreen buttons.
- Local-reader fullscreen is distraction-free: Paneleo sidebar, reader toolbar,
  and footer controls are hidden so only the comic remains.
- Escape or F11 exits local-reader fullscreen.
- Exiting fullscreen restores the previous state correctly:
  maximized returns to maximized; windowed returns to the saved size/position.
- Fit Page re-renders after fullscreen transitions for the new viewport size.
- Closing Paneleo while fullscreen preserves the prior maximized/windowed state.

Retained from beta8.3:
- Click right/left halves of local CBZ/CBR/PDF pages to turn pages.
- Manga mode reverses click zones automatically.
- Previous / page selector / Next controls remain in the local reader.
- Keyboard arrow navigation remains available.

Regression locks:
- BatCave BrowserWidget unchanged from beta8.3.
- Cover/cache methods unchanged from beta8.3.

Run INSTALL.bat once, then RUN.bat.
