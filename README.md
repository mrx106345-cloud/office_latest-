# YourDoc

A Pryonx product.

A local, offline desktop PDF & Office toolkit — a private alternative to
web-based services like iLovePDF. **Nothing is uploaded, nothing is tracked.** Every
document is processed on your own computer, inside the application itself.

YourDoc is commercial software: it needs a licence key from Pryonx to install or
run. See [Licence](#licence) and [Activation](#activation) below.

> **Rebranded from DocCore.** YourDoc is the same offline toolkit under the
> Pryonx brand. It installs side by side with a DocCore installation and uses
> its own settings folder, so upgrading is a deliberate act: uninstall DocCore
> when you are ready, and copy anything you want to keep out of
> `%APPDATA%\DocCore`.

---

## Features

| Category | Tools |
| --- | --- |
| **Tools** | PDF → Excel, PDF → Images, Images → PDF, Merge, Excel → PDF, Word → PDF, Word → Excel, Excel → Word |

Highlights:

* **Real background processing.** Every job runs on a thread pool, streams
  progress back to the UI, and can be cancelled. Outputs are written atomically,
  so a cancelled run never leaves a corrupt half-file.
* **Configurable storage.** Choose where results go, and where YourDoc keeps its
  own settings and optional local history.
* **Native drag & drop** when `tkinterdnd2` is available, with a clean
  click-to-browse fallback when it is not.
* **Branded, but never in the way.** The Pryonx monogram sits beside the
  product name in the sidebar, a 40%-visibility watermark is centred behind the
  tool screens, and an animated splash covers the first moments of startup.
* **Bounded on purpose.** Very large inputs are capped rather than allowed to
  exhaust memory. See [Limits](#limits).

---

## Limits

These exist so a pathological file produces a clear message instead of a hung
window or a dead process. Each is set from measurement, well above work a user
would reasonably expect to do.

| Input | Limit | What happens |
| --- | --- | --- |
| Rows per worksheet (Excel → PDF, Excel → Word) | 10,000 | Converted, and the result is **marked incomplete** |
| Columns per worksheet | 512 | Converted, and the result is **marked incomplete** |
| Total decoded pixels per Images → PDF job | 300 M (~900 MB decoded) | Refused with a message; nothing is written |
| Worksheet XML part size | 64 MB | Column widths fall back to Excel's defaults |
| DPI for PDF → Images | 36–1200 | Refused with a message |

**About the spreadsheet truncation.** A sheet over the row or column limit is
still converted, because refusing outright would be worse for a user with a
large-but-workable file. But a truncated sheet produces a document that looks
completely normal in any other tool, so the truncation is stated in three
places: the status banner, the details line, **and inside the output file**
itself — as a note on the last PDF page, or a paragraph in the `.docx`. If you
open the result anywhere else and do not see that note, you would not know the
file was incomplete.

A 10,000-row cap is not arbitrary. Measured on this machine, rendering runs
~0.06 s per row at 50 columns, so a 100,000-row sheet is roughly 100 minutes
and 4,000 pages. The cap makes that ~10 minutes. Peak memory measures ~538
bytes per cell, so the cap also holds the working set near 270 MB rather than
several gigabytes.

The image limit is a pixel budget rather than a file count, because pixels are
what map to memory: a decoded 12-megapixel photo costs ~35 MB, so 100 of them
is ~3.5 GB. A *single* oversized image is already guarded; this bounds the
aggregate.

---

## Install (Windows)

1. Run `YourDoc-<version>-Setup.exe`.
2. Accept the licence terms and enter the licence key Pryonx issued for this
   machine (see [Activation](#activation)). Setup will not complete without a
   key that verifies on this computer.
3. Launch **YourDoc** from the Start menu.

Need help? Open **Help & support** in the sidebar and click
**info.pryonx@gmail.com** — that opens a new message in your own mail client
with your version already filled in.

The installer is per-user by default, so it needs **no administrator prompt**.
It installs to `%LOCALAPPDATA%\Programs\YourDoc`, creates a Start-menu entry,
and registers a proper uninstaller in *Apps & features*, published as **Pryonx**.
A desktop-icon task is offered but unchecked.

The installer is **not code-signed**, so Windows SmartScreen shows an "unknown
publisher" prompt on first run. That is expected for an unsigned build; click
*More info → Run anyway*. YourDoc also carries its own installer identity, which is
why installing it does not silently replace an existing DocCore install.

Uninstalling offers to delete your settings as well; the default is to **keep**
them, so an unattended (silent) uninstall never removes data behind your back.

---
## Building from source

YourDoc is developed and built from source. See `DEVELOPING.md` in the same
folder for the setup, the test suite, and the packaging steps.

---

## Privacy statement

YourDoc contains no outbound network calls. There is no analytics SDK, no crash
reporting, no update check and no account. Every document is processed on your
own computer and nothing is uploaded.

The single exception is deliberate and user-initiated: clicking the support
address on the **Help & support** screen hands a `mailto:` link to your own mail
client so you can write to Pryonx. Nothing is sent until you press send in your
own mail application, and if no mail client is registered the click does nothing.

## Licence

YourDoc is **commercial, proprietary software**, sold one licence key per
computer. It is not free software and is not offered under the MIT License or
any other open-source licence.

Installing or using YourDoc requires a licence key issued by Pryonx. The key is
bound to the hardware fingerprint of one specific machine and carries its own
validity period (five years from issue by default, unless your invoice says
otherwise) - see **Activation** below, and `EULA.txt` for the full terms.
`NOTICE` records the trademark position: the Pryonx and YourDoc names and the
logo are not licensed for reuse.

Earlier releases were free software - MIT, and AGPL-3.0 before that, because
they linked against AGPL-licensed rendering components. Those components have
since been replaced with permissively licensed ones, listed with their own
licences in `THIRD_PARTY_NOTICES.md`, which is in the install folder. That
change is what made a proprietary relicence possible without a rewrite; it
does not apply retroactively to those older releases.

### Activation

1. Install and start YourDoc. Without a valid key it opens an activation
   window instead of the tools, showing **this computer's Machine ID**.
2. Send that Machine ID to Pryonx (see **Where to report a problem** below).
3. Pryonx issues a key bound to that Machine ID and replies with it.
4. Paste the key into the activation window. YourDoc verifies it locally -
   signature, expiry date and machine binding - on every launch, with no
   network call, no phone-home and no account.

Replacing a network adapter or reinstalling Windows changes the Machine ID, and
the licence stops verifying on that machine until it is reissued. This is
deliberate, and is explained in `EULA.txt`.

A licence key is separate from the code-signing certificate described under
**Install (Windows)** above: the key gates *who may run the software and on
which machine*; the certificate only identifies *who published the installer*.
The two are unrelated and neither one substitutes for the other.

### Where to report a problem

Write to **info.pryonx@gmail.com** — reachable from **Help & support** in the
sidebar. Version and operating system are filled in for you, which usually
saves a round trip.

