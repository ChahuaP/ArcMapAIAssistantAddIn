
#if VER != 0x07010100
  #error Inno Setup 7.1.1 is required
#endif
#ifndef Stage
  #error Stage is required
#endif
#define ProductName "南京市规划和自然资源局六合分局业务智慧治理系统"
#define PreviousProductName "小六-" + ProductName

[Setup]
AppId={{43CA251E-39B7-43E8-9E28-95465414C772}
AppName={#ProductName}
AppVersion={#Version}
AppPublisher=于佳民
VersionInfoProductName={#ProductName}
VersionInfoDescription={#ProductName}
DefaultDirName={localappdata}\Programs\Guizhi
DefaultGroupName={#ProductName}
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
SetupMutex=Guizhi.Desktop.Setup
CloseApplications=no
RestartApplications=no
OutputDir={#OutputDir}
OutputBaseFilename={#ProductName}-{#Version}
SetupIconFile={#Stage}\bootstrap\guizhi.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#ProductName}
UninstallDisplayIcon={app}\bootstrap\guizhi.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#Stage}\bootstrap\*"; DestDir: "{app}\bootstrap"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\core\*"; DestDir: "{code:ReleaseDirectory}\core"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\runtime\*"; DestDir: "{code:ReleaseDirectory}\runtime"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\renderer\*"; DestDir: "{code:ReleaseDirectory}\renderer"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\installation.json"; DestDir: "{code:ReleaseDirectory}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\bootstrap\pythonw.exe"; Parameters: "-I -B ""{app}\bootstrap\install_state.py"" --root ""{app}"" launch --user-data ""{localappdata}\Guizhi\Desktop"""; IconFilename: "{app}\bootstrap\guizhi.ico"
Name: "{userdesktop}\{#ProductName}"; Filename: "{app}\bootstrap\pythonw.exe"; Parameters: "-I -B ""{app}\bootstrap\install_state.py"" --root ""{app}"" launch --user-data ""{localappdata}\Guizhi\Desktop"""; IconFilename: "{app}\bootstrap\guizhi.ico"; Check: InstallDesktopIcon

[Run]
Filename: "{app}\bootstrap\pythonw.exe"; Parameters: "-I -B ""{app}\bootstrap\install_state.py"" --root ""{app}"" launch --user-data ""{localappdata}\Guizhi\Desktop"""; Description: "打开{#ProductName}"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
Type: files; Name: "{app}\active.json"
Type: files; Name: "{app}\install.lock"
Type: files; Name: "{app}\last-install-error.txt"
Type: filesandordirs; Name: "{app}\guizhi-python-components"
Type: filesandordirs; Name: "{app}\releases"

[Code]
var
  Attempt: String;
  RemoveSettings: Boolean;

function IsProductShortcut(Shortcut: String): Boolean;
var
  Target: String;
  Shell, Link: Variant;
begin
  Result := False;
  if not FileExists(Shortcut) then Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Link := Shell.CreateShortcut(Shortcut);
    Target := Link.TargetPath;
    Result := CompareText(Target, ExpandConstant('{app}\bootstrap\pythonw.exe')) = 0;
  except
    Result := False;
  end;
end;

function InstallDesktopIcon: Boolean;
begin
  Result := WizardIsTaskSelected('desktopicon') or
    IsProductShortcut(ExpandConstant('{userdesktop}\{#ProductName}.lnk')) or
    IsProductShortcut(ExpandConstant('{userdesktop}\{#PreviousProductName}.lnk')) or
    IsProductShortcut(ExpandConstant('{userdesktop}\规智.lnk'));
end;

procedure RemoveOldShortcuts(Name: String);
var
  DesktopShortcut, StartGroup, StartShortcut: String;
begin
  DesktopShortcut := ExpandConstant('{userdesktop}\') + Name + '.lnk';
  StartGroup := ExpandConstant('{userprograms}\') + Name;
  StartShortcut := StartGroup + '\' + Name + '.lnk';
  if IsProductShortcut(DesktopShortcut) then
    DeleteFile(DesktopShortcut);
  if IsProductShortcut(StartShortcut) then
  begin
    DeleteFile(StartShortcut);
    RemoveDir(StartGroup);
  end;
end;

function ReleaseDirectory(Param: String): String;
var
  Prefix: String;
  Index: Integer;
begin
  if Attempt = '' then
  begin
    Prefix := '{#Build}-' + GetDateTimeString('yyyymmddhhnnss', #0, #0);
    Index := 0;
    repeat
      Attempt := Prefix + '-' + IntToStr(Index);
      Index := Index + 1;
    until not DirExists(ExpandConstant('{app}\releases\') + Attempt);
  end;
  Result := ExpandConstant('{app}\releases\') + Attempt;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Parameters, Root: String;
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Root := ExpandConstant('{app}');
    Parameters := '-I -B "' + Root + '\bootstrap\install_state.py" --root "' + Root +
      '" activate --release "' + Attempt + '"';
    WizardForm.StatusLabel.Caption := '正在切换到新版本';
    if not Exec(Root + '\bootstrap\pythonw.exe', Parameters, Root, SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('无法切换到新版本。');
    if Code <> 0 then
      RaiseException('未能切换到新版本。详情见安装目录中的 last-install-error.txt。');
    RemoveOldShortcuts('{#PreviousProductName}');
    RemoveOldShortcuts('规智');
  end;
end;

function InitializeUninstall: Boolean;
begin
  RemoveSettings := False;
  if not UninstallSilent then
    RemoveSettings := MsgBox('是否同时清除「{#ProductName}」的本机设置和凭据？已保存的业务材料、成果和办理记录会保留。',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Profile: String;
begin
  if (CurUninstallStep = usPostUninstall) and RemoveSettings then
  begin
    Profile := ExpandConstant('{localappdata}\Guizhi\Desktop\');
    DeleteFile(Profile + 'settings.json');
    DeleteFile(Profile + 'speech.json');
    DeleteFile(Profile + 'speech-usage.json');
  end;
end;
