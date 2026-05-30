#ifndef MyAppVersion
#define MyAppVersion "0.0.0"
#endif

#ifndef MySourceDir
#define MySourceDir "..\build\release_staging\ArcMapAIAssistant"
#endif

#ifndef MyOutputDir
#define MyOutputDir "..\release"
#endif

[Setup]
AppId={{A0EB8F34-2B74-4C45-BF41-7A4F7B3C1E68}
AppName=GeoPilot
AppVersion={#MyAppVersion}
AppPublisher=GeoPilot
DefaultDirName={autopf}\GeoPilot
DefaultGroupName=GeoPilot
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=GeoPilotSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=no
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=GeoPilot

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{tmp}\GeoPilotPackage"; Flags: recursesubdirs createallsubdirs deleteafterinstall
Source: "{#MySourceDir}\packaging\uninstall.ps1"; DestDir: "{app}\packaging"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\GeoPilot\帮助"; Filename: "{app}\help.html"; WorkingDir: "{app}"
Name: "{autoprograms}\GeoPilot\卸载 GeoPilot"; Filename: "{uninstallexe}"; IconFilename: "{app}\uninstall.ico"

[Run]
Filename: "powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{tmp}\GeoPilotPackage\packaging\install.ps1"" -InstallDir ""{app}"" -Quiet"; StatusMsg: "正在安装 GeoPilot..."; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\uninstall.ps1"" -Quiet"; Flags: runhidden waituntilterminated; RunOnceId: "GeoPilotCleanup"
