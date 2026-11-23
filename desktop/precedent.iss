; precedent for Windows - per-user installer (no admin rights). build.py compiles it with:
;   iscc /DAppVersion=1.0.0 /DSourceDir=<dist\precedent> /DOutputDir=<dist> /DIconFile=<build\precedent.ico> precedent.iss
; Installs to %LOCALAPPDATA%\Programs\precedent, adds precedent to the Start menu (and optionally the desktop) and lists it
; in Settings > Apps. The window keeps its data (what you saved in it) in %LOCALAPPDATA%\precedent, which
; uninstalling leaves in place so a reinstall finds it again.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\precedent"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif
#ifndef IconFile
  #define IconFile "build\precedent.ico"
#endif

[Setup]
AppId={{E81247BA-698B-569E-A6DE-E1E1C366EEFB}
AppName=precedent
AppVersion={#AppVersion}
AppVerName=precedent {#AppVersion}
AppPublisher=Juwen Lie
AppPublisherURL=https://juw12.github.io/precedent/
AppSupportURL=https://github.com/juw12/precedent
DefaultDirName={autopf}\precedent
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=precedent-Windows-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\precedent.exe
UninstallDisplayName=precedent
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\precedent"; Filename: "{app}\precedent.exe"; Comment: "Stops AI coding agents from saying they cannot without evidence"
Name: "{userdesktop}\precedent"; Filename: "{app}\precedent.exe"; Comment: "Stops AI coding agents from saying they cannot without evidence"; Tasks: desktopicon

[Run]
Filename: "{app}\precedent.exe"; Description: "{cm:LaunchProgram,precedent}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\precedent.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "Stopprecedent"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { Updating: close an open precedent window first so its files can be replaced. }
  if FileExists(ExpandConstant('{app}\precedent.exe')) then
    Exec(ExpandConstant('{app}\precedent.exe'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
