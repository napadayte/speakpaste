; ==========================================================================
; SpeakPaste - Inno Setup Installer Script
; Push-to-talk voice-to-text for Windows (NVIDIA CUDA)
; ==========================================================================

#define MyAppName      "SpeakPaste"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "SpeakPaste"
#define MyAppURL       "https://github.com/napadayte/whisper_ptt"
#define MyAppExeName   "SpeakPaste.vbs"

[Setup]
; Unique application identifier - DO NOT change between versions
AppId={{B7E3F4A2-9C1D-4E5F-8A6B-2D3C4E5F6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={userappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
LicenseFile=
OutputDir=Output
OutputBaseFilename=SpeakPaste_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=..\SpeakPaste.ico
UninstallDisplayIcon={app}\SpeakPaste.ico
WizardImageFile=compiler:WizModernImage-IS.bmp
WizardSmallImageFile=compiler:WizModernSmallImage-IS.bmp
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=SpeakPaste - Voice to Text
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nSpeakPaste is a push-to-talk voice-to-text application. Hold a hotkey to record your voice, release to transcribe and paste the text into any application.%n%nRequirements:%n  - Python 3.10 or later%n  - NVIDIA GPU with CUDA-compatible drivers%n  - Microphone%n%nThe installer will set up a Python virtual environment and install all required dependencies automatically.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked
Name: "autostart"; Description: "Start SpeakPaste automatically when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; Main application script
Source: "..\speakpaste_windows.py"; DestDir: "{app}"; Flags: ignoreversion
; Post-install script
Source: "post_install.bat"; DestDir: "{app}"; Flags: ignoreversion
; Silent launcher
Source: "SpeakPaste.vbs"; DestDir: "{app}"; Flags: ignoreversion
; Icon file
Source: "..\SpeakPaste.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "SpeakPaste - Voice to Text"
Name: "{group}\Edit Configuration"; Filename: "notepad.exe"; Parameters: """{app}\.env"""; Comment: "Edit SpeakPaste settings"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional)
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "SpeakPaste - Voice to Text"; Tasks: desktopicon

[Registry]
; Auto-start on login (optional)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SpeakPaste"; ValueData: "wscript.exe ""{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Post-install: create venv and install dependencies
Filename: "{cmd}"; Parameters: "/c ""{app}\post_install.bat"" ""{app}"""; WorkingDir: "{app}"; StatusMsg: "Setting up Python environment and installing dependencies (this may take several minutes)..."; Flags: runhidden waituntilterminated
; Offer to launch after install
Filename: "wscript.exe"; Parameters: """{app}\{#MyAppExeName}"""; WorkingDir: "{app}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
; Kill SpeakPaste before uninstall
Filename: "taskkill"; Parameters: "/F /IM pythonw.exe /FI ""WINDOWTITLE eq SpeakPaste"""; Flags: runhidden

[UninstallDelete]
; Clean up venv, cache, and generated files
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\.env"
Type: files; Name: "{app}\SpeakPaste.bat"
Type: files; Name: "{app}\post_install.bat"
Type: files; Name: "{app}\post_install.log"

[Code]
// =========================================================================
// Pascal Script: Python & GPU checks, progress feedback
// =========================================================================

var
  PythonPath: String;
  PythonFound: Boolean;
  PythonVersion: String;
  NvidiaFound: Boolean;

// Execute a command and capture its output
function ExecAndCapture(const Cmd, Params: String; var Output: String): Boolean;
var
  TmpFile: String;
  ResultCode: Integer;
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := False;
  Output := '';
  TmpFile := ExpandConstant('{tmp}\speakpaste_check.txt');
  if Exec('cmd.exe', '/C ' + Cmd + ' ' + Params + ' > "' + TmpFile + '" 2>&1',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if LoadStringsFromFile(TmpFile, Lines) then
    begin
      for I := 0 to GetArrayLength(Lines) - 1 do
      begin
        if Output <> '' then
          Output := Output + #13#10;
        Output := Output + Lines[I];
      end;
    end;
    Result := (ResultCode = 0);
    DeleteFile(TmpFile);
  end;
end;

// Find Python executable (tries python, python3, py)
function FindPython: Boolean;
var
  Output: String;
begin
  Result := False;

  // Try 'python'
  if ExecAndCapture('python', '--version', Output) then
  begin
    PythonPath := 'python';
    PythonVersion := Output;
    Result := True;
    Exit;
  end;

  // Try 'python3'
  if ExecAndCapture('python3', '--version', Output) then
  begin
    PythonPath := 'python3';
    PythonVersion := Output;
    Result := True;
    Exit;
  end;

  // Try 'py' (Windows Python Launcher)
  if ExecAndCapture('py', '-3 --version', Output) then
  begin
    PythonPath := 'py -3';
    PythonVersion := Output;
    Result := True;
    Exit;
  end;
end;

// Parse major.minor from "Python X.Y.Z" string
function ParsePythonMinorVersion(const VersionStr: String): Integer;
var
  P, DotPos: Integer;
  NumStr: String;
begin
  Result := 0;
  P := Pos('Python ', VersionStr);
  if P > 0 then
  begin
    NumStr := Copy(VersionStr, P + 7, Length(VersionStr));
    // Find first dot (after major version)
    DotPos := Pos('.', NumStr);
    if DotPos > 0 then
    begin
      // Get everything after first dot
      NumStr := Copy(NumStr, DotPos + 1, Length(NumStr));
      // Find second dot or end
      DotPos := Pos('.', NumStr);
      if DotPos > 0 then
        NumStr := Copy(NumStr, 1, DotPos - 1);
      Result := StrToIntDef(NumStr, 0);
    end;
  end;
end;

function ParsePythonMajorVersion(const VersionStr: String): Integer;
var
  P, DotPos: Integer;
  NumStr: String;
begin
  Result := 0;
  P := Pos('Python ', VersionStr);
  if P > 0 then
  begin
    NumStr := Copy(VersionStr, P + 7, Length(VersionStr));
    DotPos := Pos('.', NumStr);
    if DotPos > 0 then
      NumStr := Copy(NumStr, 1, DotPos - 1);
    Result := StrToIntDef(NumStr, 0);
  end;
end;

// Check NVIDIA GPU
function CheckNvidia: Boolean;
var
  Output: String;
begin
  Result := ExecAndCapture('nvidia-smi', '', Output);
end;

// Open URL in default browser
procedure OpenURL(const URL: String);
var
  ResultCode: Integer;
begin
  ShellExec('open', URL, '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
end;

// Called before install begins
function InitializeSetup(): Boolean;
var
  MajorVer, MinorVer: Integer;
  Msg: String;
begin
  Result := True;

  // --- Check Python ---
  PythonFound := FindPython;
  if not PythonFound then
  begin
    Msg := 'Python 3.10 or later is required but was not found on this system.' + #13#10 + #13#10 +
           'Would you like to download Python from python.org?' + #13#10 + #13#10 +
           'After installing Python, make sure to check "Add Python to PATH" ' +
           'during the Python installation, then run this installer again.';
    if MsgBox(Msg, mbError, MB_YESNO) = IDYES then
      OpenURL('https://www.python.org/downloads/');
    Result := False;
    Exit;
  end;

  // Check version >= 3.10
  MajorVer := ParsePythonMajorVersion(PythonVersion);
  MinorVer := ParsePythonMinorVersion(PythonVersion);

  if (MajorVer < 3) or ((MajorVer = 3) and (MinorVer < 10)) then
  begin
    Msg := 'SpeakPaste requires Python 3.10 or later.' + #13#10 +
           'Detected: ' + PythonVersion + #13#10 + #13#10 +
           'Would you like to download a newer version from python.org?';
    if MsgBox(Msg, mbError, MB_YESNO) = IDYES then
      OpenURL('https://www.python.org/downloads/');
    Result := False;
    Exit;
  end;

  // --- Check NVIDIA ---
  NvidiaFound := CheckNvidia;
  if not NvidiaFound then
  begin
    Msg := 'NVIDIA GPU drivers were not detected (nvidia-smi not found).' + #13#10 + #13#10 +
           'SpeakPaste requires an NVIDIA GPU with CUDA support for fast transcription.' + #13#10 + #13#10 +
           'You can continue the installation, but SpeakPaste may not work correctly ' +
           'without NVIDIA drivers and CUDA.' + #13#10 + #13#10 +
           'Would you like to continue anyway?';
    if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDNO then
    begin
      OpenURL('https://www.nvidia.com/Download/index.aspx');
      Result := False;
      Exit;
    end;
  end;
end;

// Save the detected Python path for the post_install script
procedure CurStepChanged(CurStep: TSetupStep);
var
  PythonCfgFile: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Write detected python path so post_install.bat can use it
    PythonCfgFile := ExpandConstant('{app}\python_path.txt');
    SaveStringToFile(PythonCfgFile, PythonPath, False);
  end;
end;

// Custom info on the finished page
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    if NvidiaFound then
      WizardForm.FinishedLabel.Caption :=
        'SpeakPaste has been installed successfully.' + #13#10 + #13#10 +
        'Python: ' + PythonVersion + #13#10 +
        'NVIDIA GPU: Detected' + #13#10 + #13#10 +
        'On first launch, SpeakPaste will download the Whisper AI model (~3 GB).' + #13#10 +
        'Make sure you have a stable internet connection.' + #13#10 + #13#10 +
        'Default hotkey: Hold Right Alt to record, release to transcribe.' + #13#10 +
        'Settings: Edit the .env file in the installation folder.'
    else
      WizardForm.FinishedLabel.Caption :=
        'SpeakPaste has been installed successfully.' + #13#10 + #13#10 +
        'Python: ' + PythonVersion + #13#10 +
        'NVIDIA GPU: NOT DETECTED - SpeakPaste may not work correctly.' + #13#10 + #13#10 +
        'Please install NVIDIA drivers from nvidia.com/drivers before launching.' + #13#10 + #13#10 +
        'Default hotkey: Hold Right Alt to record, release to transcribe.' + #13#10 +
        'Settings: Edit the .env file in the installation folder.';
  end;
end;
