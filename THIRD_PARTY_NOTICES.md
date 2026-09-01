# Third-party notices

Paneleo 0.1.0-beta.1 is built with the components below. Paneleo's own source is licensed under AGPL-3.0-only. Third-party components remain under their own licenses.

The full license texts referenced here are in the `licenses` directory. Exact corresponding source for copyleft components shipped in the Windows binaries is included in `Paneleo-Corresponding-Source-0.1.0-beta.1.zip` on the release page.

## PyMuPDF and MuPDF

- PyMuPDF 1.28.2
- MuPDF 1.28.2
- Copyright Artifex Software, Inc. and contributors
- Open-source license: GNU Affero General Public License version 3

PyMuPDF is offered under the GNU Affero General Public License v3 or an Artifex commercial license. Paneleo uses and distributes it under the open-source option. MuPDF source notices state GNU Affero General Public License version 3 or later; Paneleo distributes the combined work under AGPL-3.0-only by selecting version 3.

See `licenses/AGPL-3.0.txt` and the PyMuPDF and MuPDF source archives in the corresponding-source package.

## PySide6, Shiboken, and Qt

- PySide6 6.11.2
- PySide6 Addons 6.11.2
- PySide6 Essentials 6.11.2
- Shiboken6 6.11.2
- Qt 6.11.2
- Copyright The Qt Company Ltd and Qt contributors
- Open-source licenses used for this distribution: LGPL-3.0-only and, for components that offer it, compatible GPL terms

Paneleo links to Qt dynamically. Recipients may replace the Qt and PySide shared libraries with compatible modified builds, and Paneleo places no restriction on reverse engineering performed to debug such modifications. See `licenses/LGPL-3.0.txt` and `licenses/GPL-3.0.txt`.

The complete corresponding Qt and PySide source archives are included in the corresponding-source package.

## Qt WebEngine and Chromium components

- Qt WebEngine 6.11.2
- Chromium 140.0.7339.225 and its third-party components

Qt WebEngine contains Chromium and many separately licensed third-party components. Their copyright notices and license identifiers are preserved in `licenses/Qt-WebEngine-6.11.2-Third-Party-Components.html`. The common LGPL-2.1, LGPL-3.0, GPL-3.0, and Apache-2.0 license texts are also included in `licenses`.

The complete Qt 6.11.2 source archive in the corresponding-source package contains the Qt WebEngine and Chromium source and its upstream license files.

## Python

- CPython 3.12.10
- Copyright Python Software Foundation and contributors
- Python Software Foundation License Version 2 and incorporated historical notices

See `licenses/Python-3.12.10.txt`.

## OpenSSL

- OpenSSL 3.0.16, used by Python's TLS support
- Copyright The OpenSSL Project Authors
- Apache License 2.0

See `licenses/OpenSSL-3.0.16.txt`.

## PyInstaller

- PyInstaller 6.22.2 runtime and bootloader
- Copyright PyInstaller Development Team and earlier contributors
- GPL-2.0-or-later with the PyInstaller bootloader exception; runtime hooks are under Apache-2.0

See `licenses/PyInstaller-6.22.2.txt` and `licenses/Apache-2.0.txt`.

## Inno Setup

- Inno Setup 7.1.0 installer and uninstaller runtime
- Copyright (C) 1997-2026 Jordan Russell
- Portions Copyright (C) 2000-2026 Martijn Laan

See `licenses/Inno-Setup-7.1.0.txt`.

## Microsoft Visual C++ runtime

Paneleo does not redistribute Microsoft Visual C++ runtime DLLs. The Windows installer and portable build use the compatible Microsoft Visual C++ 2015-2022 x64 Redistributable installed on the computer. Microsoft recommends central installation of this runtime so it can be serviced independently.

## 7-Zip

7-Zip is not bundled. Paneleo detects a separately installed copy when CBR/RAR extraction is requested. Its license and source are therefore not part of the Paneleo distributions.

