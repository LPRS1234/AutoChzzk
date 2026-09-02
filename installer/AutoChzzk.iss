#define MyAppName "AutoChzzk"
#define MyAppVersion "1.2.3"
#define MyAppPublisher "LPRS1234"
#define MyAppExeName "AutoChzzk.exe"

#ifndef MyBuildDir
  #define MyBuildDir "..\dist\AutoChzzk"
#endif

#ifndef MyOutputDir
  #define MyOutputDir "..\release"
#endif

[Setup]
AppId={{9B7E3FE4-A937-4DAD-A0B5-4330AE0885BA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir={#MyOutputDir}
OutputBaseFilename=AutoChzzk-Setup-{#MyAppVersion}
SetupIconFile=..\assets\logo\app-icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\chrome_extension\*"; DestDir: "{app}\chrome_extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { Give an automatic update launch time to shut down the tray and local server cleanly. }
  if ExpandConstant('{param:AUTOUPDATE|0}') = '1' then
    Sleep(2000);
  { Close the running tray instance so its executable can be replaced. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#MyAppExeName}"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "AutoChzzk 실행"; Flags: nowait postinstall
