#define MyAppName "OS AI Core"
#define MyAppVersion "0.1.0-uat"
#define MyAppPublisher "OS AI Core"
#define MyAppExeName "OS-AI-Core-Service.exe"

[Setup]
AppId={{D49162EF-5CA2-4E89-8B4F-E253405EB3F1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\OS AI Core
DefaultGroupName=OS AI Core
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\installer-dist
OutputBaseFilename=OS-AI-Core-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=OS AI Core
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\scripts\windows\install-service.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\scripts\windows\uninstall-service.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\OS AI Core Dashboard"; Filename: "http://127.0.0.1:8765/"
Name: "{autodesktop}\OS AI Core Dashboard"; Filename: "http://127.0.0.1:8765/"; Tasks: desktopicon
Name: "{group}\Uninstall OS AI Core"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\install-service.ps1"" -InstallDir ""{app}"""; Flags: runhidden waituntilterminated
Filename: "http://127.0.0.1:8765/"; Description: "Open the OS AI Core Dashboard"; Flags: shellexec postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\uninstall-service.ps1"""; Flags: runhidden waituntilterminated
