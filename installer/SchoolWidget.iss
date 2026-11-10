; 학교 위젯 설치 프로그램 (Inno Setup 6)
;
; 빌드 순서
;   1) PyInstaller 로 폴더형 실행 파일 만들기 -> build_onedir\SchoolWidget\SchoolWidget.exe
;   2) ISCC.exe installer\SchoolWidget.iss   -> dist_installer\SchoolWidget-Setup-<버전>.exe
; build_installer.bat 이 위 과정을 한 번에 해줍니다. (GitHub Actions 에서도 같은 방식으로 빌드)

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "학교 위젯"
#define MyAppExe "SchoolWidget.exe"

[Setup]
AppId={{6F3C2B8E-4A1D-4E7B-9C55-2D8A1B7E3F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=SchoolWidget
DefaultDirName={localappdata}\Programs\SchoolWidget
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 관리자 권한 없이 현재 사용자에게 설치 (학교 PC 에서도 설치 가능)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist_installer
OutputBaseFilename=SchoolWidget-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
#if FileExists(CompilerPath + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"
Name: "autostart"; Description: "Windows 시작 시 자동 실행"; GroupDescription: "추가 작업:"

[Files]
Source: "..\build_onedir\SchoolWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autoprograms}\{#MyAppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
; 프로그램 설정의 "Windows 시작 시 자동 실행" 과 같은 이름을 사용 (SchoolWidget.lnk)
Name: "{userstartup}\SchoolWidget"; Filename: "{app}\{#MyAppExe}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "학교 위젯 실행하기"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#MyAppExe}"; Flags: runhidden; RunOnceId: "StopSchoolWidget"

[UninstallDelete]
; 프로그램에서 켠 자동 실행 항목도 함께 정리 (시간표·일정 데이터는 %APPDATA%\SchoolWidget 에 남겨둠)
Type: files; Name: "{userstartup}\SchoolWidget.vbs"
Type: files; Name: "{userstartup}\SchoolWidget.lnk"

[Code]
// 업데이트 설치 시 실행 중인 위젯을 먼저 종료
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#MyAppExe}', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
