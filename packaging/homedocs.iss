; Inno Setup script for HomeDocs (used by build_windows.bat).
; Installs for the current user only, so no administrator password is needed.

#define AppName "HomeDocs"
#define AppVersion "0.3.0"

[Setup]
AppId={{C657A569-32A9-40AC-B9DE-36E5A29CAB64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
DefaultDirName={localappdata}\Programs\HomeDocs
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..
OutputBaseFilename=HomeDocsSetup
SetupIconFile=..\homedocs-icon.ico
UninstallDisplayIcon={app}\HomeDocs.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; an update (Check for updates in the app) closes the running HomeDocs first
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"

[Files]
Source: "..\build\dist\HomeDocs\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\HomeDocs.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\HomeDocs.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HomeDocs.exe"; Description: "Open HomeDocs now"; Flags: nowait postinstall skipifsilent
; after an update from inside the app (setup /SILENT /relaunch=1), open HomeDocs again
Filename: "{app}\HomeDocs.exe"; Flags: nowait; Check: Relaunch

; The index, models and settings live in %LOCALAPPDATA%\homedocs and are kept on uninstall,
; so reinstalling picks up where you left off.

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
