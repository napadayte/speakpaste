' ==========================================================================
' SpeakPaste Silent Launcher
' Launches SpeakPaste without a visible console window.
' ==========================================================================

Dim installDir, pythonw, script, fso

Set fso = CreateObject("Scripting.FileSystemObject")

' Resolve the directory this .vbs file lives in
installDir = fso.GetParentFolderName(WScript.ScriptFullName)

pythonw = fso.BuildPath(installDir, "venv\Scripts\pythonw.exe")
script  = fso.BuildPath(installDir, "speakpaste_windows.py")

' Verify files exist before launching
If Not fso.FileExists(pythonw) Then
    MsgBox "Python virtual environment not found." & vbCrLf & vbCrLf & _
           "Expected: " & pythonw & vbCrLf & vbCrLf & _
           "Please reinstall SpeakPaste or run post_install.bat manually.", _
           vbExclamation, "SpeakPaste"
    WScript.Quit 1
End If

If Not fso.FileExists(script) Then
    MsgBox "SpeakPaste script not found." & vbCrLf & vbCrLf & _
           "Expected: " & script & vbCrLf & vbCrLf & _
           "Please reinstall SpeakPaste.", _
           vbExclamation, "SpeakPaste"
    WScript.Quit 1
End If

' Launch hidden (0 = SW_HIDE), non-blocking (False)
Set oShell = CreateObject("WScript.Shell")
oShell.CurrentDirectory = installDir
oShell.Run """" & pythonw & """ """ & script & """", 0, False
