# Third-party notices

YourDoc itself is proprietary commercial software, licensed under the End User
Licence Agreement (see [EULA.txt](EULA.txt) and [LICENSE](LICENSE)). It
bundles the third-party packages listed below, each under its own licence. Those
terms apply to those components; none of them are covered by the EULA covering
YourDoc, and **none of them is copyleft**.

This file is a summary for convenience. **The authoritative licence text for each
package is the `LICENSE` / `COPYING` file inside that package's `*.dist-info`
directory** in the installed payload, for example:

```
%LOCALAPPDATA%\Programs\YourDoc\_internal\reportlab-5.0.1.dist-info\LICENSE.txt
```

If this summary and that file ever disagree, that file wins.

## Bundled components

Versions match `requirements.txt` and the pins the test suite is validated
against. Regenerate this list after changing a pin.

† *Transitive:* pulled in by another package rather than pinned directly, so
its version follows from the direct pin.

| Package | Version | Licence | Role in YourDoc |
| --- | --- | --- | --- |
| pypdf | 6.18.0 | BSD-3-Clause | Merge, page ranges, encrypted-PDF handling |
| pypdfium2 | 5.13.0 | Apache-2.0 OR BSD-3-Clause | Page rasterisation (PDF → Images) |
| PDFium | † | BSD-3-Clause | The renderer engine `pypdfium2` binds to; ships `pdfium.dll` |
| reportlab | 5.0.1 | BSD-3-Clause | Word/Excel → PDF writer (canvas + Platypus) |
| DejaVuSans | 2.37 | DejaVu Fonts Licence (permissive) | Unicode fallback typeface shipped at `assets/fonts/DejaVuSans.ttf` |
| pdfplumber | 0.11.10 | MIT | Text and table extraction (PDF → Word, PDF → Excel) |
| pdfminer.six | 20260107 † | MIT | Text extraction (via pdfplumber) |
| python-docx | 1.2.0 | MIT | .docx reader / writer engine (PDF → Word, Excel → Word) |
| pandas | 3.0.6 | BSD 3-Clause | Tabular assembly |
| numpy | 2.5.3 † | BSD 3-Clause | Numeric support for pandas |
| openpyxl | 3.1.5 | MIT | .xlsx reader / writer engine |
| Pillow | 12.3.0 | MIT-CMU (HPND) | Image encode / decode, PDF → Images, Images → PDF |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | AES backend pypdf needs for encrypted PDFs |
| customtkinter | 6.0.0 | MIT | Dark-themed Tkinter widgets |
| tkinterdnd2 | 0.4.2 | MIT | Native drag & drop (optional, graceful fallback) |
| charset-normalizer | 3.5.1 † | MIT | Text decoding (via pdfminer.six) |
| PyInstaller | 6.22.3 | GPL-2.0-or-later with a bundling exception | Build-time only — **not** shipped in the payload |

Transitive packages that also appear in `_internal` carry their own licences in
their respective `*.dist-info` directories.

## Why there is no AGPL component

Earlier releases linked against **PyMuPDF**, which is dual licensed under
AGPL-3.0 or a commercial Artifex licence. Because YourDoc linked against it from
its own source, the combined work had to be distributed under the AGPL — which is
what obliged those releases to publish their source.

That is **a deliberate choice to remove, not an oversight**. Every AGPL
component has been replaced with a permissively licensed one:

| Replaced | Replacement | Licence |
| --- | --- | --- |
| PyMuPDF (rendering + writing) | pypdfium2 / PDFium, and reportlab | BSD-3-Clause / Apache-2.0 |
| pdf2docx (PDF → Word) | pdfplumber + python-docx | MIT |
| opencv-python-headless (layout analysis for pdf2docx) | *(removed with pdf2docx)* | — |

The result is that YourDoc is MIT licensed and imposes no source-disclosure
obligation, so the source is no longer distributed. Every bundled dependency is
now MIT, BSD, Apache-2.0, MIT-CMU or DejaVu permissive.

This note records the change so the reasoning is not rediscovered later. It is
not legal advice.

## Other notes

* The frozen build is produced with **PyInstaller**, whose own licence permits
  bundling. It is a build-time dependency and is not part of the shipped
  application.
* `assets/pryonx-logo-source.jpg` is the only first-party artwork committed to
  the repository. Every other image in `assets/` is derived from it by
  `tools/make_brand_assets.py` and is excluded from version control.
* YourDoc performs no network I/O at runtime, so no package is fetched or
  phoned home by the application itself.
* PDF → Word reads the PDF's text layer. It reproduces content and reading order,
  not the exact page layout, and a scanned PDF needs OCR first.