# Security

## Trust boundaries
Paneleo treats downloaded comic archives/PDFs, imported backup files, embedded web content, and remote cover artwork as untrusted input.

## Current hardening
- BatCave is the only site allowed to navigate inside the embedded browser.
- Local-file access from web content is disabled.
- CBZ/CBR archive extraction is limited and validated.
- Image/PDF raster sizes are bounded.
- Backup imports are size-limited, validated and sanitized.
- Direct runtime dependencies are pinned and the Windows x64 installer uses hash verification.
- BatCave cover discovery/fetching runs inside the existing BatCave WebEngine origin; no second hidden browser window/page is created.
- Cover page and image URLs are validated as HTTPS BatCave/subdomain URLs and image byte/decoded-dimension limits remain enforced.
- Paneleo may persist a reduced JPEG thumbnail of BatCave cover/poster artwork in its AppData cover cache so the UI can restore covers after restart. Comic reader pages are never written by this cover-cache feature.
- BatCave thumbnail filenames are SHA-256-derived identifiers rather than remote paths or titles.
- The BatCave thumbnail cache is bounded to 160 files and older thumbnails are pruned.
- In-memory cover caches remain bounded.
- Paneleo 2.0 beta keeps the hardened v1.4.6 reader/browser behavior while restructuring the surrounding UI.

## Reporting
Do not post private backup files or crash logs publicly without reviewing them first; they may contain local filesystem paths.

Paneleo is not yet a public security-reviewed release. These controls are defense-in-depth and do not guarantee that every malicious document or web exploit is prevented.
