# Developing YourDoc

This file covers building, testing and packaging YourDoc. For what the app
does and how to install it, read the end-user `README.md` in the same folder.


## Branding

The Pryonx artwork is not committed pre-made. `assets/pryonx-logo-source.jpg` is
the only binary input, and everything else is derived from it:

```bash
python tools/make_brand_assets.py            # refresh assets/ after a logo change
python tools/make_brand_assets.py --force    # rebuild everything
```

| Generated file | Used for |
| --- | --- |
| `pryonx-logo.png` / `pryonx-mark.png` | full lockup and monogram, white background keyed out |
| `yourdoc-watermark.png` | the lockup pre-blended over `#0F1115` |
| `yourdoc-center-mark.png` | the large monogram centred behind the tool screens |
| `yourdoc-splash.png` / `-pulse.png` | the two frames the splash cross-fades between |
| `yourdoc.ico` / `yourdoc.png` | English application icon (16–256 px) and a 512 px PNG |
| `brand-preview.html` | a self-contained page showing all of it in context |

Three details are worth knowing if you touch this code:

* **Tk cannot fade a widget.** There is no per-widget alpha, so each background
  image is pre-blended against the window colour at build time and the label
  itself stays fully opaque. `branding.WATERMARK_OPACITY` and
  `branding.CENTER_MARK_OPACITY` are the knobs.
* **The unmatting cannot key on brightness alone.** The artwork is coloured, not
  black, so `255 - min(r,g,b)` is wrong: it crushes a saturated red's green
  channel to zero and deletes the monogram's white bevel highlights (19.7% of
  the ink). The background is found by flood-filling inward from the border
  instead. Counters inside the lettering are *also* enclosed paper-white, so they
  are cleared explicitly below the band that `_text_band_start()` locates; the
  bevels sit above it and are kept.
* **The frozen build keeps its artwork in `sys._MEIPASS`.** Asset paths must go
  through `branding.asset_path()`; `build.py` adds `assets/` to the bundle
  and the e2e layer asserts the files really shipped.

The app itself performs **no background network I/O**, and the logo pipeline is
Pillow and numpy only — a test in `tests/security/test_offline.py` generates the
whole asset set with every socket call replaced by an exception.

The one outbound path is user-initiated: the support address on the Help screen
(`branding.open_mail_link()`) hands a `mailto:` link to the operating system's
mail client. Nothing is sent or fetched unless the user clicks it, and failures
are swallowed so a missing mail client cannot affect the app.

Startup flags: `python app.py --no-splash` skips the animation, and
`python app.py --self-check report.json` writes a JSON runtime report (used by the
packaging tests and handy for support).

## Run from source

```bash
git clone <your-fork-url> yourdoc
cd yourdoc

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Python 3.10 – 3.14 are supported. That floor is declared in
`pyproject.toml` as `requires-python = ">=3.10"` and enforced by a test, rather
than being a claim in this file only — the code uses PEP 604 unions
(`str | None`) throughout, so an older interpreter would fail at import with a
syntax error instead of a useful message. `pyproject.toml` also carries the
project metadata; it deliberately does **not** repeat the dependency pins, which
live in `requirements.txt` so there is one list rather than two that drift.

## Where YourDoc stores data

All of this is configurable from **Settings → Data & Storage**.

| What | Default location | Controlled by |
| --- | --- | --- |
| `config.json` (preferences) | `%APPDATA%\YourDoc\` | *Data folder* setting |
| `history.jsonl` (optional local log) | `%APPDATA%\YourDoc\` | *Data folder* + history toggle |
| `data_location.txt` (bootstrap pointer) | `%APPDATA%\YourDoc\` | written automatically when you move the data folder |
| Your results | next to each source file | *Default output folder* setting |

Notes:

* **Save results next to the original file** (default on) puts a converted copy
  of `report.pdf` in the same folder as `report.pdf`. Turn it off to send every
  result to one folder instead.
* **Never overwrite** (default on) appends `_1`, `_2` … instead of replacing an
  existing file. This is applied when the destination is proposed *and* enforced
  by the engine for multi-file outputs.
* The history log is **off by default**. When enabled it is plain-text JSONL you
  can read or delete, and it is capped so it cannot grow without bound.
* If you move the data folder, YourDoc writes a one-line pointer in the old
  default location so the choice survives a restart. Moving back removes it.

macOS uses `~/Library/Application Support/YourDoc`; Linux uses
`$XDG_CONFIG_HOME/YourDoc` or `~/.config/YourDoc`.

---

## Project layout

```
yourdoc/
├── app.py                      # entry point: window, sidebar, routing, landing page
├── build.py                    # PyInstaller packaging automation
├── build_installer.py          # freeze, compile the installer, optionally sign it
├── pyproject.toml              # project metadata + the supported Python range
├── requirements.txt
├── LICENSE
├── THIRD_PARTY_NOTICES.md      # bundled packages and their licences
├── assets/                     # brand source + generated artwork (see Branding)
├── installer/
│   └── yourdoc.iss             # Inno Setup script
├── tools/
│   ├── make_brand_assets.py    # derives assets/ from the Pryonx logo
│   └── run_tests.py            # one command for the whole suite + coverage gates
├── tests/
│   ├── smoke_test.py           # headless engine tests
│   ├── ui_smoke.py             # builds every screen and drives Settings
│   ├── installer_test.py       # install -> launch -> uninstall cycle
│   └── perf/                   # scale and timing budgets on realistic inputs
└── src/
    ├── config.py               # preferences + local data storage
    ├── engine/                 # pure logic, no UI imports
    │   ├── errors.py           # exception hierarchy with user-facing messages
    │   ├── task_runner.py      # thread pool, progress + cancellation plumbing
    │   ├── deps.py             # lazy optional-dependency imports
    │   ├── pdf_core.py         # merge and page-range helpers
    │   ├── pdf_converters.py   # PDF -> Word, Excel, images
    │   └── office.py           # Word/Excel -> PDF, Word <-> Excel
    └── ui/
        ├── theme.py            # palette, geometry, typography
        ├── branding.py         # brand identity, asset paths, logo derivation
        ├── splash.py           # animated startup screen
        ├── components.py       # drop zone, file list, options, progress
        ├── views.py            # one screen per tool + the tool registry
        └── settings_view.py    # Settings screen (storage paths)
```

### Architecture rules

1. **`src/engine` never imports `src/ui`.** Engine functions are plain callables
   that accept a `progress` callback and a `cancel_event`, which makes them
   testable without a display.
2. **Tkinter is single-threaded.** Worker callbacks are marshalled onto the UI
   thread by `src.ui.components.EventBridge`, which drains a queue on a periodic
   `after()` tick. Never touch a widget from a worker — including *reading* one,
   because `tkinter.Variable.get()` is an unsynchronised interpreter call and
   two workers entering Tcl at once can hang the window rather than raise. That
   is why every tool reads its options through `snapshot_options()` on the UI
   thread and receives them as a plain dict. A test parses each `execute()` to
   keep it that way.
3. **Nothing writes in place.** Every output goes to a temporary sibling and is
   promoted with `os.replace` only on success. The temporary name keeps the
   destination's extension, because `openpyxl` and `pandas` reject anything
   else, and carries a random token so two workers cannot collide on it.
4. **Adding a tool is additive.** Subclass `BaseToolView`, set a handful of class
   attributes, implement `build_options()` and `execute()`, then append it to
   `VIEW_CLASSES` in `src/ui/views.py`. Navigation, validation, progress,
   cancellation, and error reporting come for free.

---

## Testing

The suite is built on **pytest**, organised as a full pyramid, and driven by one
entry point. Install the test tools first:

```bash
pip install -r requirements.txt -r requirements-dev.txt

python tools/run_tests.py            # fast layers + coverage gates
python tools/run_tests.py --fuzz     # add property-based / fuzz tests
python tools/run_tests.py --perf     # add the scale / timing budgets
python tools/run_tests.py --all      # everything, including the installer e2e
python tools/run_tests.py --report   # verbose, listing every outcome
```

Coverage gates are enforced by the runner: **90%** for `src/engine` and **80%**
for everything shipped. Because the gates exist only to catch a regression, the
runner treats being unable to evaluate them as a failure too: a missing or
unreadable `.coverage` file fails the run rather than quietly passing. The
previous run's data is deleted first, so a stale file can never satisfy the
thresholds. Pass `--no-cov` to skip coverage and the gates together.

| Layer | Location | What it proves |
| --- | --- | --- |
| Unit | `tests/unit/` | Each helper in isolation, with fakes for the heavy parts |
| Integration | `tests/integration/` | Operations on real PDFs, asserting outcomes not file existence |
| Security | `tests/security/` | No network use, and paths stay put |
| Property / fuzz | `tests/property/` | Grammar invariants over generated input; malformed bytes never crash |
| Perf / scale | `tests/perf/` | Realistically-shaped inputs stay inside a time budget, and doubling the rows does not quadruple the time |
| UI | `tests/ui/` | Every screen constructs, plus real widget interactions |
| Regression | `tests/regression/` | One named test per bug fixed in development |
| E2E | `tests/e2e/` | Installs, launches and uninstalls the built app; verifies the frozen build |

The perf layer exists because the fixtures used to be two columns wide, which
hid a quadratic cell write in the Word exporter: a 300x15 sheet took 227
seconds where a single pass over the table rows takes under one. The budget
tests use a realistic column count, so that class of bug cannot come back
unnoticed. They are opt-in with `--perf`.

The slow layers are opt-in, so a normal run stays fast; they show up as explicit
skips rather than simply vanishing. The historical standalone scripts still work
and now delegate to the suite:

```bash
python tests/smoke_test.py      # engine unit + integration
python tests/ui_smoke.py        # the UI layer
python tests/installer_test.py  # the built installer, end to end
```

Branding gets the same treatment: the logo derivation is tested pixel by pixel
(white keyed to transparent, colours preserved when re-composited over white, the
watermark blended at exactly 40% against the real window colour), the splash is
driven until it finishes and skipped via both a click and a keypress, and the e2e
layer asserts the artwork is actually present in the frozen bundle and the
installed payload.

**Diagnostics.** The built app can report its runtime environment:

```bash
YourDoc.exe --self-check report.json
```

That writes the app version and whether it is running frozen - useful for
support, and used by the e2e tests to check the packaged build.

---

## Building a standalone executable

```bash
python build.py                 # one-folder build in dist/YourDoc/
python build.py --onefile       # single portable executable
python build.py --console       # keep a console for debugging startup crashes
python build.py --clean         # wipe build/ and dist/ first
```

`build.py` handles the parts that trip up a naive PyInstaller invocation:
CustomTkinter's data files, Tcl/Tk DLL discovery on Windows, `tkinterdnd2`'s
prebuilt Tcl libraries, and the lazily imported `pdfminer`/`PIL`/`pandas`
submodules.

## Building the installer

```bash
python build_installer.py                # freeze, then compile the installer
python build_installer.py --skip-freeze  # recompile only the installer
```

Needs Inno Setup 6 6.3 or newer:

```bash
winget install --id JRSoftware.InnoSetup -e
```

`build_installer.py` locates `ISCC.exe` via `INNO_SETUP_ISCC`, `PATH`, the usual
install folders, and the registry, then writes
`dist\YourDoc-<version>-Setup.exe`. The version comes from `app.py`'s
`__version__`, which is the single source of truth — `installer/yourdoc.iss`
deliberately has no default and fails to compile without it, so a hand-run
`ISCC.exe` cannot ship a stale number.

### Code signing

The installer is **unsigned by default**, so Windows SmartScreen shows an
"unknown publisher" warning and the user has to click *More info → Run anyway*.
Signing is wired up but inert until a certificate is configured:

```bash
# a .pfx file, optionally with a password
set YOURDOC_SIGN_PFX=D:\certs\yourdoc.pfx
set YOURDOC_SIGN_PFX_PASSWORD=...

# or a certificate already installed in the machine store
set YOURDOC_SIGN_CERT=<thumbprint>

python build_installer.py --require-signature
```

`--require-signature` makes a missing or failing signature a hard error, which
is what a real release should use so an unsigned installer is never published
by accident. Without the flag, signing is attempted and a failure is only a
warning. `signtool.exe` is found on `PATH`, under the Windows SDK, or via
`YOURDOC_SIGNTOOL`.

### About the payload size

The one-folder build is dominated by third-party native code. The largest
contributors after removing the AGPL stack are:

| Component | Needed for |
| --- | --- |
| PDFium (`pdfium.dll`) | page rasterisation for PDF → Images |
| pandas + numpy | PDF → Excel |
| Pillow, pdfminer, cryptography, … | images, table extraction, AES |
| reportlab, pypdf | the Word/Excel → PDF writer and PDF core |

Removing PyMuPDF, pdf2docx and OpenCV cut roughly 160 MB from the payload, and
with them the last copyleft dependency. There is no longer a
`opencv-python-headless` trade-off to consider: OpenCV is gone entirely.

