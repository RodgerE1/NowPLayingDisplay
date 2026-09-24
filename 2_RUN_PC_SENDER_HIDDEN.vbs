Option Explicit

Dim fileSystem
Dim shell
Dim projectFolder
Dim pythonExecutable
Dim senderScript
Dim command

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonExecutable = fileSystem.BuildPath( _
    projectFolder, ".venv\Scripts\pythonw.exe")
senderScript = fileSystem.BuildPath(projectFolder, "pc_sender.py")

If Not fileSystem.FileExists(pythonExecutable) Then
    MsgBox "Python environment not found." & vbCrLf & _
           "Run 1_INSTALL_PC_SENDER.bat first.", _
           16, "ESP32 PC Sender"
    WScript.Quit 1
End If

If Not fileSystem.FileExists(senderScript) Then
    MsgBox "pc_sender.py was not found.", _
           16, "ESP32 PC Sender"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectFolder
command = Chr(34) & pythonExecutable & Chr(34) & _
          " " & Chr(34) & senderScript & Chr(34)

shell.Run command, 0, False
