' AtmosTune Invisible Background Launcher
' Executes run_atmostune.bat with zero window flashing
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "run_atmostune.bat", 0, False
