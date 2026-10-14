Option Explicit
Dim shell, fso, base, pythonw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base
pythonw = "pythonw.exe"
On Error Resume Next
shell.Run """" & pythonw & """ """ & base & "\start.pyw" & """", 0, False
If Err.Number <> 0 Then
    MsgBox "Python was not found. Install Python 3.10+ and enable Add Python to PATH.", 16, "fire"
End If
