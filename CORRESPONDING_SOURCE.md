# Corresponding source

Each Paneleo binary release includes a corresponding-source archive on its GitHub release page.

For Paneleo 0.1.0-beta.1, download:

`Paneleo-Corresponding-Source-0.1.0-beta.1.zip`

The archive contains:

- the exact Paneleo source revision used for the installer and portable build;
- build, installer, test, and packaging scripts;
- pinned dependency versions and hashes;
- license and third-party notice files;
- the official PyMuPDF 1.28.2 source distribution;
- the official MuPDF 1.28.2 source distribution, including its third-party source;
- the official PySide6/Shiboken 6.11.2 source distribution;
- the official complete Qt 6.11.2 source distribution, including Qt WebEngine and Chromium;
- the official PyInstaller 6.22.2 source distribution; and
- a manifest with the source archive URLs, sizes, and SHA-256 hashes.

The included Paneleo README and build scripts describe the Windows build process. The binary dependency lockfile is `requirements-win64.txt`.

Paneleo's source can also be viewed at the Git tag matching the release. The release archive is the preferred compliance copy because it also carries the exact third-party sources used by the bundled components.

Questions about obtaining or rebuilding the source can be opened as a GitHub Issue in the Paneleo repository.

