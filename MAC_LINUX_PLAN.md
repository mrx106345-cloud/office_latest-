# Mac & Linux distribution plan (no Apple Developer account)

Goal: ship license-gated YourDoc as **real installer files** for macOS and Linux
without owning a Mac and without paying Apple. The Windows build is already done
(`dist/YourDoc.exe`, gate verified).

---

## 1. Reality check (read first)

* **PyInstaller freezes for the OS it runs on.** From this Windows PC it can only
  emit `.exe`. A `.deb`/`.rpm` needs a real Linux kernel; a `.dmg`/`.app` needs
  real macOS. Git Bash is MSYS on the Windows kernel (`sys.platform = win32`) —
  it does **not** count as Linux.
* **Therefore the files are built in GitHub Actions cloud runners** (already
  configured in `.github/workflows/build.yml`): `ubuntu-latest` for Linux,
  `macos-latest` for Mac. You push code, the cloud builds, you download the
  artifact. **You never hand customers a script** — they get only the file.
* **No Apple account is required to produce a working Mac file** — we ad-hoc sign
  (free). What an account buys is a *silent* first launch (see §4).

---

## 2. What was changed to make this work

* `src/license.py` — machine fingerprint is now cross-platform:
  * Linux: hardware adapters under `/sys/class/net` (skips `lo`, bridges, tun,
    docker), stable fallback `/etc/machine-id`.
  * macOS: hardware ports via `networksetup`, stable fallback `IOPlatformUUID`.
  * Windows: unchanged (`getmac` + `MachineGuid`). Covered by unit tests.
* `build_linux_package.py` — one command → `.deb` **and** `.rpm` into `dist/`.
* `build_macos_package.py` — one command → `.app` + `.dmg`, **ad-hoc signed by
  default** so it actually launches on Apple Silicon (an unsigned Mach-O will not).
* `.github/workflows/build.yml` — builds all Linux + Mac artifacts in the cloud.

---

## 3. How to produce the files (one time; no Mac needed)

1. Put this project on GitHub (your action — this reverses the earlier
   "no-git" rule, and it is a proprietary codebase, so decide public vs private).
   * Public repo → macOS/Linux build minutes are **free**.
   * Private repo → macOS minutes are billed at 10x weight.
2. GitHub → **Actions** → *Build distributable installers* → **Run workflow**.
3. Download from the run's **Artifacts**:
   * `YourDoc-1.3.0.deb`, `YourDoc-1.3.0.rpm`
   * `YourDoc-1.3.0.dmg`
4. Those three files + the existing `YourDoc-1.3.0-Setup.exe` are the full
   distribution set. Customers receive only these.

Licensing still works everywhere: the key is bound to the machine fingerprint,
which §2 now computes correctly on each OS.

---

## 4. What happens AFTER the unsigned Mac file is delivered (the real cost)

An ad-hoc signed, **not notarized** `.dmg` runs fine, but because it was
downloaded it carries the `com.apple.quarantine` flag, so on **first open**
Gatekeeper blocks it. This is expected, not a bug.

**One-time buyer action (pick whichever you document for them):**

* macOS 14 and earlier: right-click the app → **Open** → **Open**. Done once.
* macOS 15 Sequoia (Apple removed the right-click override):
  System Settings → **Privacy & Security** → scroll to the "YourDoc was blocked"
  note → **Open Anyway**. Done once.
* Universal command any user can run once:
  ```sh
  xattr -dr com.apple.quarantine "/Applications/YourDoc.app"
  ```
After that single approval the app opens normally forever.

**To make first launch silent** you must notarize — that is the only thing that
requires the $99/yr Apple Developer ID. Nothing else about YourDoc needs it;
re-run `build_macos_package.py --sign "Developer ID Application: … " --notarize`
when (and if) you get an account.

---

## 5. Linux — no equivalent problem

`.deb`/`.rpm` have **no Apple-style signing gate**. Customers just install:

```sh
sudo apt install ./YourDoc-1.3.0.deb      # Debian/Ubuntu/Mint
sudo rpm -i  YourDoc-1.3.0.rpm            # Fedora/RHEL/openSUSE
```

No warning, no override, no account. This is why Linux is easy unsigned.

---

## 6. Honest limitations

* I cannot run GitHub Actions from this machine, so the workflow is best-effort
  and may need 1–2 tweaks on first run (usually the macOS Tk link or fpm setup).
* Unsigned Mac distribution is trusted-once-by-the-user, not trusted-by-default.
  For a commercial product that matters to you, budget for the Apple Developer ID
  later and re-run the same script with `--sign`.
* The master private key stays in `%LOCALAPPDATA%\Pryonx` and is never sent to CI;
  only the **public** key (already in `src/license.py`) travels with the source.
