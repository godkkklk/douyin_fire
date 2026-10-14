#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{9F2D0F94-C797-4F08-AE94-638A98258D4F}
AppName=火宝
AppVersion={#AppVersion}
AppPublisher=火宝项目
DefaultDirName={localappdata}\Programs\HuoBao
DefaultGroupName=火宝
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=releases
OutputBaseFilename=HuoBao-Setup-{#AppVersion}-win-x64
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\HuoBao.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "dist\HuoBao\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\火宝"; Filename: "{app}\HuoBao.exe"
Name: "{autodesktop}\火宝"; Filename: "{app}\HuoBao.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HuoBao.exe"; Description: "启动火宝"; Flags: nowait postinstall skipifsilent
