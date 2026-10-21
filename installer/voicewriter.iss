; VoiceWriter installer (Inno Setup 6). Build with installer\build.ps1.
; Installs per user, without admin rights. Python and the packages are downloaded during setup.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{95E594FB-FAA0-4013-8AAD-2C1882EEF5FC}
AppName=VoiceWriter
AppVersion={#AppVersion}
AppPublisher=Austen Cloud
AppPublisherURL=https://github.com/austencloud/voicewriter
AppSupportURL=https://github.com/austencloud/voicewriter/issues
DefaultDirName={localappdata}\Programs\VoiceWriter
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=VoiceWriter-Setup-{#AppVersion}
SetupIconFile=voicewriter.ico
UninstallDisplayIcon={app}\voicewriter.ico
UninstallDisplayName=VoiceWriter
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Running copies are closed by the code below, matched by install folder.
CloseApplications=no

[Tasks]
Name: startup; Description: "Start VoiceWriter when I sign in to Windows"

[InstallDelete]
; Old code files from an earlier version, so a removed module can't linger.
Type: files; Name: "{app}\voicewriter\*.py"
Type: filesandordirs; Name: "{app}\voicewriter\__pycache__"

[Files]
Source: "..\voicewriter\*.py"; DestDir: "{app}\voicewriter"; Flags: ignoreversion
Source: "..\launch.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "voicewriter.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "setup-env.cmd"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "bin\uv.exe"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "bin\uv-LICENSE-MIT"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "bin\uv-LICENSE-APACHE"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\VoiceWriter"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\launch.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\voicewriter.ico"; Comment: "Dictation bar"

[Registry]
; Same command the app's own "Start with Windows" switch writes.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoiceWriter"; ValueData: """{app}\.venv\Scripts\pythonw.exe"" ""{app}\launch.pyw"""; Tasks: startup

[Run]
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: """{app}\launch.pyw"""; WorkingDir: "{app}"; Description: "Start VoiceWriter now"; Flags: postinstall nowait skipifsilent; Check: SetupWorked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\voicewriter\__pycache__"
Type: files; Name: "{app}\install.log"
Type: dirifempty; Name: "{app}"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

var
  EnvReady: Boolean;

function SetupWorked: Boolean;
begin
  Result := EnvReady;
end;

{ Closes every VoiceWriter process started from this install folder (the launcher and the app),
  and nothing else: a copy run from anywhere else is left alone. }
procedure CloseRunningCopies;
var
  Dir, Cmd: String;
  Code, I: Integer;
begin
  Dir := ExpandConstant('{app}') + '\';
  StringChangeEx(Dir, '''', '''''', True);
  Cmd := '-NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith(''' + Dir + ''', ''OrdinalIgnoreCase'') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"';
  { Twice, in case the launcher restarted the app between the two kills. }
  for I := 1 to 2 do
    Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningCopies;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Log: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  WizardForm.StatusLabel.Caption := 'Downloading Python and the speech libraries (about 2 GB). This takes a few minutes...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  EnvReady := Exec(ExpandConstant('{cmd}'), '/c ""' + ExpandConstant('{app}\tools\setup-env.cmd') + '""',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  WizardForm.ProgressGauge.Style := npbstNormal;
  if not EnvReady then
  begin
    Log := ExpandConstant('{app}\install.log');
    SuppressibleMsgBox('VoiceWriter couldn''t download Python or its speech libraries. Check your internet connection and run setup again.'
      + #13#10#13#10 + 'Details: ' + Log, mbError, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value, Data: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    CloseRunningCopies;
    { The startup entry may come from the task above or the app's own menu switch.
      Remove it only when it points at this install. }
    if RegQueryStringValue(HKCU, RunKey, 'VoiceWriter', Value) and (Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Value)) > 0) then
      RegDeleteValue(HKCU, RunKey, 'VoiceWriter');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\VoiceWriter');
    if DirExists(Data) and (SuppressibleMsgBox('Also delete your VoiceWriter history, saved audio, word list and settings?' + #13#10#13#10
      + 'They are in ' + Data + '.' + #13#10#13#10
      + 'The speech models (about 3 GB) are shared with other Whisper programs and stay in '
      + ExpandConstant('{%USERPROFILE}') + '\.cache\huggingface\hub. Delete the folders named models--Systran--faster-whisper there to free that space.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
      DelTree(Data, True, True, True);
  end;
end;
