' AtmosTune Background Silent Runner
' Launches python main.py --telegram completely hidden in the background without any CMD window

Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory
WshShell.Run "pythonw.exe main.py --telegram", 0, False
Set WshShell = Nothing
