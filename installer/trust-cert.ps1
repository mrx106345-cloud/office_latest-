<#
.SYNOPSIS
  One-time trust of the Pryonx self-signed code-signing certificate, so Windows
  shows the publisher as "Pryonx" (not "Unknown publisher") and reports the
  installer's signature as Valid.

.DESCRIPTION
  YourDoc is signed with a self-signed certificate -- free, no certificate
  authority -- which means a fresh computer does not already trust it. This
  script does the one thing that fixes that: it installs the public certificate
  into the two local-machine stores Authenticode consults.

  It only changes the *publisher / signature* display. It does NOT change the
  blue SmartScreen "Windows protected your PC" screen, which is a download-
  reputation check no certificate can satisfy on a brand-new file. See
  TRUSTING.md for the full, honest picture.

  Safe and reversible: this adds a single trusted certificate. You can remove it
  later from certlm.msc > "Trusted Root Certification Authorities".

.PARAMETER CerPath
  Path to the Pryonx .cer shipped beside the installer. Defaults to
  "Pryonx.cer" next to this script.

.PARAMETER VerifyFile
  Optional. A signed .exe to re-check afterwards, to confirm the status flipped
  to Valid.

.EXAMPLE
  # Run once, from an elevated PowerShell (the script self-elevates if needed):
  .\trust-cert.ps1
  .\trust-cert.ps1 -VerifyFile .\YourDoc-1.4.0-Setup.exe
#>
[CmdletBinding()]
param(
    [string]$CerPath = (Join-Path $PSScriptRoot 'Pryonx.cer'),
    [string]$VerifyFile
)

$ErrorActionPreference = 'Stop'

# --- re-launch elevated, because the LocalMachine store needs administrator ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Not running as administrator -- relaunching elevated...' -ForegroundColor Yellow
    $argLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    if ($CerPath)     { $argLine += ' -CerPath "{0}"' -f $CerPath }
    if ($VerifyFile)  { $argLine += ' -VerifyFile "{0}"' -f $VerifyFile }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argLine -Verb RunAs
        exit
    } catch {
        Write-Host 'Elevation was declined. Run this script from an elevated PowerShell, or use the manual steps in TRUSTING.md.' -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-Path $CerPath)) {
    Write-Host "Certificate not found: $CerPath" -ForegroundColor Red
    Write-Host 'Pass -CerPath pointing at the Pryonx.cer shipped with the installer.' -ForegroundColor Yellow
    exit 1
}

# Thumbprint of the file we are about to trust, so the user can cross-check it
# against the value published on the download page.
$pending = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 ([System.IO.Path]::GetFullPath($CerPath))
Write-Host ('Installing certificate: {0}' -f $pending.Subject) -ForegroundColor Cyan
Write-Host ('  SHA-1 thumbprint : {0}' -f $pending.Thumbprint)

foreach ($storeName in @('Root', 'TrustedPeople')) {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store $storeName, 'LocalMachine'
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    $already = $store.Certificates | Where-Object { $_.Thumbprint -eq $pending.Thumbprint }
    if ($already) {
        Write-Host ("  {0}: already present, skipped" -f $storeName) -ForegroundColor DarkGray
    } else {
        $store.Add($pending)
        Write-Host ("  {0}: added" -f $storeName) -ForegroundColor Green
    }
    $store.Close()
}

Write-Host ''
Write-Host 'Done. Future YourDoc installers signed with this certificate now show publisher "Pryonx".' -ForegroundColor Green
Write-Host 'Reminder: the blue SmartScreen warning on a brand-new download is separate and is not removed by this.' -ForegroundColor Yellow

if ($VerifyFile -and (Test-Path $VerifyFile)) {
    $sig = Get-AuthenticodeSignature $VerifyFile
    Write-Host ('  {0} -> {1}' -f (Split-Path $VerifyFile -Leaf), $sig.Status)
    if ($sig.Status -ne 'Valid') {
        Write-Host '  If this is still UnknownError, an older Pryonx certificate may be installed -- see TRUSTING.md "If you installed an older Pryonx certificate".' -ForegroundColor Yellow
    }
}
