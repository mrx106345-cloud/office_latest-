# Installing YourDoc, and the warnings you may see

This page explains the warnings Windows shows when you first install YourDoc,
what they mean, and the one optional step that makes the publisher read
**Pryonx** instead of *unknown*.

**Short version, if you just want to install it:** copy the files to a folder on
your computer, run the installer, and if Windows asks, click **More info** →
**Run anyway**. Nothing is wrong with YourDoc.

## What you should have been given

Two files, together:

| File | What it is |
| --- | --- |
| `YourDoc-1.3.0-Setup.exe` | The installer |
| `Pryonx.cer` | The certificate, for the optional step below |

### About the licence

YourDoc is commercial, proprietary software: it needs a licence key issued by
Pryonx and bound to one specific computer before it will install or run.
Activation is checked locally on every launch - there is no activation server,
no phone-home and no account. The full terms are in `EULA.txt`; `LICENSE`,
`NOTICE` and `THIRD_PARTY_NOTICES.md` are installed alongside the app.

The licence key is a different thing from the code-signing certificate
discussed everywhere else on this page, and neither one replaces the other:
the key gates *who may run YourDoc, and on which machine*; the certificate
only identifies *who published the installer*.

## The easiest way to avoid every warning

**Copy the files to a folder on your computer first, then run the installer from
there.**

Windows only runs this reputation check on files that arrived from the internet.
A file that came off a USB stick, or was copied from another PC on your network,
is never checked and never warned about. If you received these files by email,
chat app, or download link, this is worth doing.

## If you did download it: what to expect

Windows will show this:

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.

This is normal for any new application. Click **More info**, then **Run
anyway**. YourDoc installs and runs.

You do not need to do anything else. But it is worth understanding why, so you
know it is not a defect.

### Why the warning appears

YourDoc's installer is signed with a certificate Pryonx generated itself rather
than one bought from a commercial certificate authority. That certificate is
genuine and valid, but **Microsoft's SmartScreen does not check
whether a certificate is genuine**. It checks a *reputation score* — built up
from how many people have safely downloaded this particular file. A brand-new
file has no reputation, so it is warned about.

This is why buying a certificate does not solve it: a commercial code-signing
certificate produces the **same warning** on first download. Reputation comes
from people installing the software, not from the certificate behind it. It is
Windows' rule for everyone, and no configuration can turn it off.

You can verify the file is what Pryonx published, which is what the signature is
actually for — see [Checking the file has not been altered](#checking-the-file-has-not-been-altered).

## The optional step: making the publisher read Pryonx

There is a second, smaller display: the yellow bar that says **Unknown
publisher**. Installing the certificate once changes that to **Pryonx**, which
is genuinely informative — it proves the file is Pryonx's and not someone else's.

Do this once per computer, not once per install:

1. Double-click `Pryonx.cer`.
2. Click **Install Certificate…**
3. Choose **Local Machine** (needs administrator rights).
4. Choose **Place all certificates in the following store**.
5. Select **Trusted Root Certification Authorities** → **Finish**.
6. Windows asks to confirm; click **Yes**.

To be clear about what this does and does not do:

| | Before | After installing `Pryonx.cer` |
| --- | --- | --- |
| Publisher bar | "Unknown publisher" | **Pryonx** |
| `Get-AuthenticodeSignature` | `UnknownError` | `Valid` |
| SmartScreen blue warning | shown | **still shown** — it is a separate check |

That last row is the honest part. The certificate fixes the publisher display;
SmartScreen is a different system entirely and is not affected by it.

### Which certificate is this?

Each time Pryonx regenerates its signing certificate the fingerprint changes, so
the `.cer` shipped beside a new installer may not be the one already trusted on
your computer. Check the current one:

```powershell
Get-FileHash .\Pryonx.cer -Algorithm SHA256 | Select-Object -ExpandProperty Hash
```

The certificate shipped with **YourDoc 1.3.0** is:

```
SHA-256  D63612D5ED965F1A5F4449B2D288069461C37184A21ED2F9B9AEB7324EC716D1
SHA-1    71BC8DA544AA8134BAF833048B080408AD6ACD71
Issued   2026-10-15, valid until 2036-10-13
```

### If you installed an older Pryonx certificate

Earlier builds were signed with a **different** certificate, so a trusted old
one will not validate the current installer. The signature check will keep
reporting `UnknownError` until the current certificate is added.

1. Press **Win+R**, run `certmgr.msc`, and go to **Trusted Root Certification
   Authorities**.
2. Delete every older certificate whose issuer is **Pryonx**.
3. Double-click the `Pryonx.cer` shipped with this installer and follow the six
   steps above.

Removing the old one matters beyond tidiness: a superseded certificate still
vouching for old builds is a signature that no longer means "current release".

### Checking it worked

Right-click either `YourDoc-1.3.0-Setup.exe` or `YourDoc.exe` → **Properties** →
**Digital Signatures**. You should see a signature from **Pryonx**.

In PowerShell:

```powershell
Get-AuthenticodeSignature .\YourDoc-1.3.0-Setup.exe | Format-List Status, SignerCertificate
```

`Status` reads `Valid` once the certificate is trusted.

## A note on corporate machines

If your IT policy blocks untrusted certificate authorities, a self-signed
certificate cannot be installed and SmartScreen may not be overridable at all.
In that case YourDoc needs a certificate from a commercial authority. Contact
**info.pryonx@gmail.com** and you will get a straight answer on whether that is
planned.

## Checking the file has not been altered

The signature covers the file contents, independent of SmartScreen. If YourDoc
has been modified since Pryonx released it, this check fails:

```powershell
Get-AuthenticodeSignature .\YourDoc-1.3.0-Setup.exe | Select-Object Status
```

A modified copy reports something other than `Valid`. If the signer is anything
other than **Pryonx**, do not run it.

---

Support: **info.pryonx@gmail.com** — also reachable from **Help & support**
inside the app, along with the website.
