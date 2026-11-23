; Relay Helper installer for Windows (#636). Per user: no administrator prompt, and the helper runs in the signed-in
; user's session, which is where ProPresenter's window lives.
; Build: makensis /DVERSION=1.2.0 /DEXE=path\relay-helper.exe /DOUT=path\installer.exe relay-helper.nsi
Unicode true
!ifndef VERSION
  !error "pass /DVERSION=..."
!endif
!ifndef EXE
  !error "pass /DEXE=path to relay-helper.exe"
!endif
!ifndef OUT
  !define OUT "Relay-Helper-setup.exe"
!endif

Name "Relay Helper"
OutFile "${OUT}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Relay Helper"
InstallDirRegKey HKCU "Software\Relay Helper" "InstallDir"
SetCompressor /SOLID lzma

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Relay Helper"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  ; A running helper holds its program file open: stop it before replacing the file.
  nsExec::Exec '"$INSTDIR\relay-helper.exe" uninstall'
  File "/oname=relay-helper.exe" "${EXE}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Relay Helper" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Relay Helper"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Relay"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ; A Start menu shortcut to a command prompt in the install folder, where `relay-helper pair ...` is typed once.
  CreateDirectory "$SMPROGRAMS\Relay Helper"
  CreateShortcut "$SMPROGRAMS\Relay Helper\Relay Helper command prompt.lnk" "$SYSDIR\cmd.exe" '/K "cd /d "$INSTDIR" && relay-helper status"'
  ; Start at every sign-in from now on, and start now. Until it is paired it waits for `relay-helper pair`.
  nsExec::Exec '"$INSTDIR\relay-helper.exe" install'
SectionEnd

Section "Uninstall"
  nsExec::Exec '"$INSTDIR\relay-helper.exe" uninstall'
  Delete "$INSTDIR\relay-helper.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\Relay Helper\Relay Helper command prompt.lnk"
  RMDir "$SMPROGRAMS\Relay Helper"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "Software\Relay Helper"
  ; The settings (the pairing) in %APPDATA%\Relay Helper are kept, so a reinstall does not need a new code.
SectionEnd
