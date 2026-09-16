; ArcMap Harness one-click installer.
; Build with packaging\build_installer.ps1 (runs build_harness.ps1 first, then
; ISCC). The installer automatically removes every previous GeoPilot /
; ArcMapAIAssistant / ArcMap Harness version before deploying.

#define MyAppName "ArcMap Harness"
#ifndef MyAppVersion
  #define MyAppVersion Trim(FileRead(FileOpen(AddBackslash(SourcePath) + "..\VERSION")))
#endif
#ifndef MySourceDir
  #define MySourceDir "..\build\harness-staging"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\release"
#endif

[Setup]
AppId={{D8461F1B-DBDB-48FA-985D-00F76F4E337C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=ArcMap Harness
DefaultDirName={autopf}\ArcMap Harness
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=ArcMapHarnessSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=no
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\harness\bridge\ArcMapBridge.exe

[Files]
; Whole staged deployment is unpacked to a temp dir, then install_harness.ps1
; copies it to {app}\harness and wires up the Add-in and dsh profile.
Source: "{#MySourceDir}\*"; DestDir: "{tmp}\harness-package"; Flags: recursesubdirs createallsubdirs ignoreversion deleteafterinstall
; Persistent helpers used by the uninstaller / manual runs.
Source: "uninstall_harness.ps1"; DestDir: "{app}\packaging"; Flags: ignoreversion
; Helpers extracted on demand and never installed.
Source: "install_harness.ps1"; Flags: dontcopy
Source: "install_profile.ps1"; Flags: dontcopy
Source: "legacy_cleanup.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\harness"
Type: filesandordirs; Name: "{app}\packaging"

[Code]
function ShellExe(): String;
begin
  if FileExists(ExpandConstant('{pf}\PowerShell\7\pwsh.exe')) then
    Result := ExpandConstant('{pf}\PowerShell\7\pwsh.exe')
  else if FileExists(ExpandConstant('{pf32}\PowerShell\7\pwsh.exe')) then
    Result := ExpandConstant('{pf32}\PowerShell\7\pwsh.exe')
  else
    Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function RunPs(const ScriptFile, Params: String): Integer;
var
  ResultCode: Integer;
begin
  if not Exec(ShellExe(), '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + ScriptFile + '" ' + Params,
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := -1
  else
    Result := ResultCode;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { Remove every previous version before any file is written. }
  ExtractTemporaryFile('legacy_cleanup.ps1');
  Code := RunPs(ExpandConstant('{tmp}\legacy_cleanup.ps1'), '-Quiet');
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Params: String;
begin
  if CurStep = ssPostInstall then
  begin
    ExtractTemporaryFile('install_harness.ps1');
    ExtractTemporaryFile('install_profile.ps1');
    Params := '-NoElevate -Stage "' + ExpandConstant('{tmp}\harness-package') +
              '" -InstallDir "' + ExpandConstant('{app}') + '"';
    Code := RunPs(ExpandConstant('{tmp}\install_harness.ps1'), Params);
    if Code <> 0 then
      RaiseException('ArcMap Harness 安装脚本失败，退出码：' + IntToStr(Code));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Script: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Script := ExpandConstant('{app}\packaging\uninstall_harness.ps1');
    if FileExists(Script) then
      Code := RunPs(Script, '-Quiet -InstallDir "' + ExpandConstant('{app}') + '"');
  end;
end;
