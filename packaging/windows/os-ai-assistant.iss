; Per-user installer for the OS AI Assistant desktop app (no administrator rights).
#define MyAppName "OS AI Assistant"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif
#define MyAppPublisher "OS AI Core"
#define MyAppExeName "OS-AI-Assistant.exe"

[Setup]
AppId={{6A0B7E52-1C49-4C3B-9E0E-5D8F2A7C4B11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\OS AI Assistant
DefaultGroupName=OS AI Assistant
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\installer-dist
OutputBaseFilename=OS-AI-Assistant-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=OS AI Assistant
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\OS AI Assistant"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall OS AI Assistant"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OS AI Assistant"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start OS AI Assistant now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop a running copy so its files can be removed. Settings and the data folder are kept.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "StopAssistant"
