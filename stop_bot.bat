@echo off
title Stop AtmosTune Bot
echo Stopping AtmosTune background processes...
taskkill /f /fi "WINDOWTITLE eq AtmosTune Telegram Bot Controller*" >nul 2>&1
wmic process where "commandline like '%%main.py --telegram%%'" delete >nul 2>&1
echo Done! AtmosTune Telegram bot has been stopped.
pause
