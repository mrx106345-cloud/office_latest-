; ---------------------------------------------------------------------------
; YourDoc - Windows installer (Inno Setup)
;
; Compile with the helper script, which locates ISCC and supplies the version:
;     python build_installer.py
;
; AppVersion is deliberately NOT defaulted here. A hardcoded fallback silently
; drifts from app.py's __version__, so an installer gets built and shipped with
; the wrong number in Apps & features and the file name. Compile through
; build_installer.py, or pass it yourself:
;     ISCC.exe installer\yourdoc.iss /DAppVersion=1.4.0
;
; Requires Inno Setup 6.3 or newer (for x64compatible). The script installs
; per-user by default so no administrator prompt is needed, but offers the
; choice via PrivilegesRequiredOverridesAllowed.
;
; Commercial gate: Setup will not finish unless the license key entered on the
; wizard's key page verifies on *this* computer (checked by running the
; installed exe itself, not a reimplementation in Pascal, so signature,
; expiry and machine-binding logic all live in exactly one place).
; ---------------------------------------------------------------------------

#ifndef AppVersion
  #error AppVersion is not defined. Build with `python build_installer.py`, \
    or pass /DAppVersion=<x.y.z> on the ISCC command line. app.py's \
    __version__ is the single source of truth.
#endif

#define AppName "YourDoc"
#define AppCompany "Pryonx"
#define AppPublisher "Pryonx"
#define AppExeName "YourDoc.exe"
; Support is by email. There is deliberately no AppUrl here: Inno shows those as
; clickable links, and a link to a repository that does not exist looks broken
; to the user. The address lives in the app under Help & support instead.
#define SupportEmail "info.pryonx@gmail.com"

; Stable, unique application identifier. Never change this between releases:
; Windows uses it to match an upgrade (or uninstall) to an existing install.
;
; This AppId is deliberately new. YourDoc was rebranded from DocCore but ships
; as its own product with its own storage folder, so installing it must not
; silently convert (or overwrite) a DocCore installation already on the machine.
; The two therefore sit side by side in Apps & features.
#define AppId "{{C41B7E52-9A3D-4F87-B2E6-5D8A1C3F7E94}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppCompany}
VersionInfoDescription={#AppName} offline PDF toolkit by {#AppCompany} - installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCopyright=Copyright (C) {#AppCompany}

; Per-user install: no UAC prompt, and the app lands in
; %LOCALAPPDATA%\Programs\YourDoc. Users who want a machine-wide install can
; pick it in the wizard, or pass /ALLUSERS on the command line.
;
; UninstallDisplayName includes the company so the Apps & features entry reads
; "YourDoc (Pryonx)" and is easy to tell apart from a leftover DocCore install.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\yourdoc.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
; Commercial software: acceptance of the EULA is a real condition of install,
; not just text shipped in the folder for whoever cares to read it later.
LicenseFile=..\EULA.txt
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The frozen application tree produced by build.py (PyInstaller --onedir).
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; The EULA, the trademark notice and the third-party notices all ship into the
; install folder too, not just onto the wizard's acceptance page: a user who
; installs from a shared copy later, or who wants the exact text on file, should
; find it next to the program.
;
; THIRD_PARTY_NOTICES.md is a licence condition, not a display preference: it
; records the licence every bundled library ships under, and those terms are
; unaffected by YourDoc's own proprietary EULA.
Source: "..\EULA.txt"; DestDir: "{app}"; DestName: "EULA.txt"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\NOTICE"; DestDir: "{app}"; DestName: "NOTICE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; DestName: "THIRD_PARTY_NOTICES.md"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
; How to install the self-signed certificate. The wizard warning cannot be
; removed for free software signed with a self-signed certificate, so the fix
; is documented next to the application rather than argued with on screen.
Source: "..\installer\TRUSTING.md"; DestDir: "{app}"; DestName: "Trusting the Pryonx certificate.txt"; Flags: ignoreversion
; The self-signed public certificate itself. Recipients are given only this
; installer, so the "install the certificate" guide cannot point at a separate
; download -- the .cer has to be in the folder the guide lives in. Produced by
; build_installer.py (and the CI windows job) into dist/. Skipped for an
; unsigned build, where there is no certificate to ship and no warning to fix.
Source: "..\dist\Pryonx.cer"; DestDir: "{app}"; DestName: "Pryonx.cer"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Comment: "Offline PDF toolkit"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The frozen build creates caches at runtime; remove them with the app.
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// ---------------------------------------------------------------------------
// Commercial gate: license key page + post-install verification.
//
// The wizard does not try to understand license keys itself - it never
// reimplements the Ed25519 check, the expiry rule or the machine fingerprint.
// It runs the freshly installed YourDoc.exe with --validate-key / --activate
// and reads the exit code, so the installer can never drift from the rules the
// app applies on every ordinary launch.
//
// Note: comments here use // rather than { }, because the Inno preprocessor
// parses a multi-line brace comment as a directive and aborts the compile.
// ---------------------------------------------------------------------------
var
  LicenseKeyPage: TInputQueryWizardPage;

procedure InitializeWizard();
begin
  // Shaped like the app's own activation dialog: the key field plus the
  // machine ID the buyer needs to send Pryonx if they do not have a key yet.
  LicenseKeyPage := CreateInputQueryPage(wpLicense,
    'License key',
    'Enter the license key Pryonx issued for this computer.',
    'YourDoc is licensed per machine. Setup will verify the key with the ' +
    'program itself once installation finishes, and will uninstall again if ' +
    'it does not match this computer.' + Chr(13) + Chr(10) + Chr(13) + Chr(10) +
    'Do not have a key yet? Install YourDoc first - the program''s activation ' +
    'window will show you this computer''s Machine ID to send to Pryonx.');
  LicenseKeyPage.Add('License key:', False);
  // {param:LicenseKey|} lets an unattended or scripted install pass the key on
  // the command line (Setup.exe /LicenseKey=YRD1-...) instead of typing it;
  // GetPreviousData remembers it for a repair or reinstall of the same user.
  LicenseKeyPage.Values[0] := GetPreviousData('LicenseKey', ExpandConstant('{param:LicenseKey|}'));
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'LicenseKey', LicenseKeyPage.Values[0]);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Key: String;
begin
  Result := True;
  if CurPageID <> LicenseKeyPage.ID then
    Exit;
  Key := Trim(LicenseKeyPage.Values[0]);
  if Key = '' then
  begin
    // SuppressibleMsgBox, not MsgBox: a plain MsgBox still pops under /SILENT
    // and would hang an unattended install instead of failing it cleanly.
    SuppressibleMsgBox('A license key is required to install YourDoc. Enter ' +
                        'the key Pryonx issued for this computer, or pass it ' +
                        'on the command line as /LicenseKey=...', mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  // Shape check only, so an obviously truncated paste is caught before files
  // hit disk. Real verification (signature, expiry, machine binding) happens
  // once YourDoc.exe itself is installed, below.
  if not (Pos('YRD1-', Key) = 1) then
  begin
    SuppressibleMsgBox('That does not look like a YourDoc license key. Keys ' +
                        'start with YRD1- followed by groups of characters. ' +
                        'Check for a typo, or paste the key again from the ' +
                        'email Pryonx sent.', mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
end;

function RunLicenseCli(const Args: String; const CaptureFile: String; out ExitCode: Integer): Boolean;
var
  Cmd: String;
begin
  // Exec() cannot capture stdout directly, so the exe's own output is
  // redirected through cmd to a temp file when the caller wants to read it
  // (only ever used for --show-machine-id, in the failure message below).
  if CaptureFile <> '' then
    Cmd := '/C ""' + ExpandConstant('{app}') + '\YourDoc.exe" ' + Args + ' > "' + CaptureFile + '""'
  else
    Cmd := '/C ""' + ExpandConstant('{app}') + '\YourDoc.exe" ' + Args + '"';
  Result := Exec(ExpandConstant('{cmd}'), Cmd, '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Key, MachineIdFile, MachineId: String;
  MachineIdRaw: AnsiString;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;

  Key := Trim(LicenseKeyPage.Values[0]);

  if not RunLicenseCli('--validate-key "' + Key + '"', '', ResultCode) then
  begin
    SuppressibleMsgBox('Setup could not start YourDoc to verify the license ' +
                        'key. Check that the file was not corrupted during ' +
                        'copy, then run setup again.', mbError, MB_OK, IDOK);
    ResultCode := 1;
  end;

  if ResultCode <> 0 then
  begin
    // Reuse the app's own machine-ID output rather than a second, drifting
    // implementation of the fingerprint here in setup code.
    MachineIdFile := ExpandConstant('{tmp}\machine-id.txt');
    MachineId := '';
    if RunLicenseCli('--show-machine-id', MachineIdFile, ResultCode) and FileExists(MachineIdFile) then
    begin
      if LoadStringFromFile(MachineIdFile, MachineIdRaw) then
        MachineId := Trim(MachineIdRaw);
      DeleteFile(MachineIdFile);
    end;
    if MachineId <> '' then
      MachineId := Chr(13) + Chr(10) + Chr(10) + 'This computer''s Machine ID is: ' + MachineId;
    SuppressibleMsgBox('The license key could not be verified on this computer.' + MachineId +
                        Chr(13) + Chr(10) + Chr(13) + Chr(10) +
                        'Setup will now remove the files it just copied. Send ' +
                        'the Machine ID above to Pryonx to get a key that ' +
                        'works here, or to renew a licence for a computer ' +
                        'whose hardware changed.', mbError, MB_OK, IDOK);
    // Uninstall silently, then close the wizard - leaving a broken, unusable
    // install behind is worse than an aborted one.
    Exec(ExpandConstant('{uninstallexe}'), '/SILENT /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    WizardForm.Close;
    Exit;
  end;

  // Same key, same rules, one more time to persist it, so the app opens
  // already-activated on first run instead of straight back into its own
  // activation dialog.
  RunLicenseCli('--activate "' + Key + '"', '', ResultCode);
end;

// ---------------------------------------------------------------------------
// Uninstall: offer to remove the user's settings and optional local history.
// They live in %APPDATA%\YourDoc so an upgrade keeps them, which is why they
// are not deleted automatically.
// ---------------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\YourDoc');
    if DirExists(DataDir) then
    begin
      // SuppressibleMsgBox, not MsgBox: a plain MsgBox is not suppressed by
      // /SILENT or /VERYSILENT and would hang an unattended uninstall. The
      // default answer is No, so an unattended uninstall always keeps the
      // user's settings rather than deleting them silently.
      if SuppressibleMsgBox('Also delete your YourDoc settings and local history?' +
                            Chr(13) + Chr(10) + Chr(13) + Chr(10) + DataDir,
                            mbConfirmation, MB_YESNO, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
