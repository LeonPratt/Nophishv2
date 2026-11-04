Option Explicit
Dim files, shell, root, python, script, command
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(root, ".venv\Scripts\pythonw.exe")
script = files.BuildPath(root, "startup.pyw")

If Not files.FileExists(python) Or Not files.FileExists(script) Then
    MsgBox "NoPhish could not start. Keep this launcher in the project folder and install the .venv first.", 16, "NoPhish"
    WScript.Quit 1
End If

shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " " & Chr(34) & script & Chr(34)
If WScript.Arguments.Named.Exists("check") Then
    WScript.Echo "NoPhish launcher paths verified; no server started."
    WScript.Quit 0
End If

' pythonw has no console; window style 0 also keeps the launcher hidden.
shell.Run command, 0, False
