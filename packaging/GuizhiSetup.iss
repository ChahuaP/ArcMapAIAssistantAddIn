
#if VER != 0x07010100
  #error Inno Setup 7.1.1 is required
#endif
#ifndef Stage
  #error Stage is required
#endif

[Setup]
AppId={{43CA251E-39B7-43E8-9E28-95465414C772}
AppName=规智
AppVersion={#Version}
AppPublisher=规智
DefaultDirName={localappdata}\Programs\Guizhi
DefaultGroupName=规智
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
SetupMutex=Guizhi.Desktop.Setup
CloseApplications=no
RestartApplications=no
OutputDir={#OutputDir}
OutputBaseFilename=GuizhiSetup-{#Build}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=规智

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Types]
Name: "light"; Description: "轻客户端"
#if (HasSpeech == "1") || (HasArcmap == "1")
Name: "standard"; Description: "标准桌面"
Name: "custom"; Description: "自定义安装"; Flags: iscustom
#endif

[Components]
#if (HasSpeech == "1") || (HasArcmap == "1")
Name: "core"; Description: "规智桌面与本机 GIS"; Flags: fixed; Types: light standard custom
#else
Name: "core"; Description: "规智桌面与本机 GIS"; Flags: fixed; Types: light
#endif
#if HasSpeech == "1"
Name: "speech"; Description: "本地语音"; Types: standard
#endif
#if HasArcmap == "1"
Name: "arcmap"; Description: "ArcGIS 扩展"; Types: standard
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#Stage}\bootstrap\*"; DestDir: "{app}\bootstrap"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\core\*"; DestDir: "{code:ReleaseDirectory}\core"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\runtime\*"; DestDir: "{code:ReleaseDirectory}\runtime"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\renderer\*"; DestDir: "{code:ReleaseDirectory}\renderer"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\payload\installation.json"; DestDir: "{code:ReleaseDirectory}"; Flags: ignoreversion
#if HasSpeech == "1"
Source: "{#Stage}\payload\speech\*"; DestDir: "{code:ReleaseDirectory}\speech"; Flags: recursesubdirs ignoreversion; Components: speech
#endif
#if HasArcmap == "1"
Source: "{#Stage}\payload\arcmap\*"; DestDir: "{code:ReleaseDirectory}\arcmap"; Flags: recursesubdirs ignoreversion; Components: arcmap
#endif

[Icons]
Name: "{group}\规智"; Filename: "{app}\bootstrap\pythonw.exe"; Parameters: "-I -B ""{app}\bootstrap\install_state.py"" --root ""{app}"" launch --user-data ""{localappdata}\Guizhi\Desktop"""
Name: "{userdesktop}\规智"; Filename: "{app}\bootstrap\pythonw.exe"; Parameters: "-I -B ""{app}\bootstrap\install_state.py"" --root ""{app}"" launch --user-data ""{localappdata}\Guizhi\Desktop"""; Tasks: desktopicon

[Run]
Filename: "{app}\bootstrap\pythonw.exe"; Parameters: "-I -B ""{app}\bootstrap\install_state.py"" --root ""{app}"" launch --user-data ""{localappdata}\Guizhi\Desktop"""; Description: "打开规智"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
Type: files; Name: "{app}\active.json"
Type: files; Name: "{app}\install.lock"
Type: files; Name: "{app}\last-install-error.txt"

[Code]
var
  Attempt: String;
  RemoveSettings: Boolean;

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
#if HasSpeech == "1"
    if WizardIsComponentSelected('speech') then Parameters := Parameters + ' --component speech';
#endif
#if HasArcmap == "1"
    if WizardIsComponentSelected('arcmap') then Parameters := Parameters + ' --component arcmap';
#endif
    WizardForm.StatusLabel.Caption := '正在切换到新版本';
    if not Exec(Root + '\bootstrap\pythonw.exe', Parameters, Root, SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('无法切换到新版本。');
    if Code <> 0 then
      RaiseException('未能切换到新版本。详情见安装目录中的 last-install-error.txt。');
  end;
end;

function InitializeUninstall: Boolean;
begin
  RemoveSettings := False;
  if not UninstallSilent then
    RemoveSettings := MsgBox('是否同时清除规智的本机设置和凭据？已保存的业务材料、成果和办理记录会保留。',
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
