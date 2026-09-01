#ifndef AppVersion
  #define AppVersion "0.1.0-beta.1"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.1.0.1"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Paneleo"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{9D895786-5852-4B76-A3EC-5598A6D62B80}
AppName=Paneleo
AppVersion={#AppVersion}
AppVerName=Paneleo {#AppVersion}
AppPublisher=Paneleo
DefaultDirName={autopf}\Paneleo
DefaultGroupName=Paneleo
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Paneleo-Setup-{#AppVersion}
SetupIconFile=generated\Paneleo.ico
UninstallDisplayIcon={app}\Paneleo.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductVersion={#AppVersionNumeric}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Paneleo"; Filename: "{app}\Paneleo.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Paneleo"; Filename: "{app}\Paneleo.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Paneleo.exe"; Description: "Launch Paneleo"; Flags: nowait postinstall skipifsilent
