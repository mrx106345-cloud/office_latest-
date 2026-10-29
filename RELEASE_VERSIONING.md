# Release Versioning, Coexistence & Licensing

This document is the policy for building and shipping **new YourDoc versions
without ever breaking an older one**. It exists so that, once a newer release is
built, the previous releases stay on disk, stay installable, keep their own
activation, and keep working — with no cross-version interference.

Authoritative source: the version number lives in `app.py` (`__version__`). The
build tool (`build_installer.py`) and the staging tool (`tools/stage_release.py`)
both AST-read that literal; nothing else hard-codes a version.

---

## 1. Golden rules

1. **One folder per release.** Every version's installers live together in
   `dist/YourDoc-<version>/` (e.g. `dist/YourDoc-1.4.0/`). Never mix two versions
   in one folder, and never delete an older folder when shipping a new one.
2. **Never overwrite the previous installer.** A new build writes its own
   version-named files; old files are left untouched. `release/` is a scratch
   staging area — `dist/YourDoc-<version>/` is the durable record.
3. **Per-version data isolation.** Each release stores its own settings,
   activation, history and automation log under
   `%APPDATA%\YourDoc\<version>` (Windows),
   `~/Library/Application Support/YourDoc/<version>` (macOS), or
   `$XDG_CONFIG_HOME/YourDoc/<version>` (Linux). See §4.
4. **Licensing is identical across versions.** Same signed-key format, same
   embedded publisher key, same offline verification. A key that activates 1.3.0
   will activate 1.4.0 — by design, the *product* is licensed, not the version.
   See §5.

---

## 2. What a "complete release" contains

`tools/stage_release.py` gathers whichever of these are present into the
version folder:

| Platform | File | Built by |
|---|---|---|
| Windows  | `YourDoc-<version>-Setup.exe` | locally (`python build_installer.py`) |
| Linux    | `YourDoc-<version>.deb`       | CI (`.github/workflows/build.yml`) |
| Linux    | `YourDoc-<version>.rpm`       | CI |
| macOS    | `YourDoc-<version>.dmg`       | CI (macOS runner) |

A platform that has not been built yet is reported `missing` — that is normal,
not an error.

---

## 3. Building a new version, step by step

Assume you are releasing `X.Y.Z`:

1. **Bump the version** in `app.py` (`__version__ = "X.Y.Z"`) and mirror it in
   `src/config.py` (`PROGRAM_VERSION = "X.Y.Z"`). A regression test
   (`tests/regression/test_version_isolation.py`) fails if these drift.
2. **Commit** on `main`, then **tag** `vX.Y.Z` and **push** both:
   ```
   git add -A
   git commit -m "YourDoc X.Y.Z: ..."
   git tag vX.Y.Z
   git push origin main --follow-tags
   ```
   The tag push triggers the cross-platform CI build.
3. **Build Windows locally:** `python build_installer.py` →
   `dist/YourDoc-X.Y.Z-Setup.exe`.
4. **Pull the CI artifacts** (`.deb`, `.rpm`, `.dmg`) back into `dist/`.
5. **Stage** everything into the version folder:
   ```
   python tools/stage_release.py
   ```
   → `dist/YourDoc-X.Y.Z/` now holds all four installers.
6. **Leave every older `dist/YourDoc-<old>/` folder exactly where it is.** They
   remain installable and remain activated on their own.

---

## 4. Coexistence and data isolation

`src/config.py` composes the data directory as `default_data_dir()` =
`<product root>/<version>`, controlled by two module constants:

- `VERSION_ISOLATION` (bool) — nest state under the version folder.
- `PROGRAM_VERSION` (str) — the folder name, kept equal to `app.py.__version__`.

**Effect:** installing 1.4.0 alongside 1.3.0 means each reads and writes its own
`config.json`, `history.jsonl`, `batch.log`, and activation. Activating one
version does **not** activate or change the other.

**Upgrade migration (so an existing user does not lose their activation):** on
first launch, if a version folder is empty but the older shared
`.../YourDoc/config.json` exists, that state is **copied** into the new version
folder (never the reverse, and never overwriting an already-initialised version
folder). The old install is left intact, so both keep working.

---

## 5. Licensing across versions

- The license scheme (`src/license.py`) is byte-for-byte identical between
  1.3.0 and 1.4.0 — same Ed25519 signature, same embedded `PUBLIC_KEY`, same
  per-machine binding, verified every launch. **Keys are cross-version
  compatible.**
- A license is **product-scoped**: it unlocks YourDoc. It is cryptographically
  bound to YourDoc and the machine, so the same key never unlocks a different
  Pryonx product.
- Because of §4 isolation, the *activation state* is stored per version even
  though the *key* is the same — activating 1.4.0 leaves 1.3.0's stored
  activation as it was.
- **Enterprise vs standard caps** are re-derived from the verified key at every
  launch (`AppConfig.sync_license`); a hand-edited `config.json` cannot fake a
  higher tier. This holds for both versions.

---

## 6. Code-signing caveat (read before shipping a rebuild)

YourDoc is signed with a **self-signed** certificate (no paid CA). Consequences:

- Each installer bundles its matching `Pryonx.cer`; recipients follow the
  in-folder trust guide (`installer/TRUSTING.md`) once per machine.
- **If the signing certificate is ever regenerated, the new build's publisher
  fingerprint changes.** Buyers who trusted an older certificate see the warning
  again and must re-trust the new `Pryonx.cer`. The 1.4.0 certificate differs
  from the original 1.3.0 one for this reason.
- Rebuilding an *older* version (e.g. recovering a missing 1.3.0 Windows `.exe`)
  signs it with whatever certificate is configured at rebuild time; its bundled
  `.cer` always matches its own `.exe`, so the folder stays self-consistent.

---

## 7. Do-not list

- Do not edit old version folders or their installers in place.
- Do not ship loose scripts to customers — only the installer `.exe`/`.deb`/
  `.rpm`/`.dmg` inside a version folder.
- Do not let `PROGRAM_VERSION` and `app.py.__version__` drift (the test stops you).
- Do not assume an activation in one version applies to another's stored state.
