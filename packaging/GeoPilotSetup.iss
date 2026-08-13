#ifndef MyAppVersion
#error MyAppVersion must be supplied from the repository VERSION file
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
Name: "{autoprograms}\GeoPilot\卸载 GeoPilot"; Filename: "{uninstallexe}"; IconFilename: "{app}\uninstall.ico"

[UninstallRun]
Filename: "pwsh.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\uninstall.ps1"" -Quiet{code:UninstallUserDataParameter}"; Flags: runhidden waituntilterminated; RunOnceId: "GeoPilotCleanup"

[Code]
var
  RemoveUserDataOnUninstall: Boolean;
  InstallScriptExitCode: Integer;

function InitializeUninstall(): Boolean;
var
  Form: TSetupForm;
  InfoLabel: TNewStaticText;
  CheckBox: TNewCheckBox;
  OkButton: TNewButton;
  CancelButton: TNewButton;
begin
  RemoveUserDataOnUninstall := False;

  Form := CreateCustomForm(ScaleX(460), ScaleY(170), False, True);
  try
    Form.Caption := '卸载 GeoPilot';

    InfoLabel := TNewStaticText.Create(Form);
    InfoLabel.Parent := Form;
    InfoLabel.Left := ScaleX(16);
    InfoLabel.Top := ScaleY(16);
    InfoLabel.Width := ScaleX(428);
    InfoLabel.Height := ScaleY(48);
    InfoLabel.Caption := '卸载 GeoPilot 会删除程序文件和 ArcMap 插件。默认保留模型配置、API Key、自建工具、工作流记录和日志。';
    InfoLabel.WordWrap := True;

    CheckBox := TNewCheckBox.Create(Form);
    CheckBox.Parent := Form;
    CheckBox.Left := ScaleX(16);
    CheckBox.Top := ScaleY(76);
    CheckBox.Width := ScaleX(428);
    CheckBox.Height := ScaleY(32);
    CheckBox.Caption := '同时删除用户配置和本地数据';
    CheckBox.Checked := False;

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Left := ScaleX(254);
    OkButton.Top := ScaleY(124);
    OkButton.Width := ScaleX(90);
    OkButton.Height := ScaleY(30);
    OkButton.Caption := '继续卸载';
    OkButton.ModalResult := mrOk;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Left := ScaleX(354);
    CancelButton.Top := ScaleY(124);
    CancelButton.Width := ScaleX(90);
    CancelButton.Height := ScaleY(30);
    CancelButton.Caption := '取消';
    CancelButton.ModalResult := mrCancel;

    Result := Form.ShowModal = mrOk;
    if Result then
      RemoveUserDataOnUninstall := CheckBox.Checked;
  finally
    Form.Free;
  end;
end;

function UninstallUserDataParameter(Param: String): String;
begin
  if RemoveUserDataOnUninstall then
    Result := ' -RemoveUserConfig'
  else
    Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Parameters: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  Parameters := ExpandConstant('-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{tmp}\GeoPilotPackage\packaging\install.ps1" -InstallDir "{app}" -Quiet');
  if not Exec('pwsh.exe', Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    InstallScriptExitCode := 1;
    RaiseException('无法启动 GeoPilot 安装脚本。');
  end;
  InstallScriptExitCode := ResultCode;
  if InstallScriptExitCode <> 0 then
    RaiseException(Format('GeoPilot 安装验证失败，退出码：%d。', [ResultCode]));
end;

function GetCustomSetupExitCode(): Integer;
begin
  Result := InstallScriptExitCode;
end;
