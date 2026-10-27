@echo off
title AtmosTune Telegram Bot Controller
cd /d "%~dp0"

echo ========================================================
echo       Starting AtmosTune Telegram Bot Standalone
echo ========================================================
echo Running independently from Antigravity IDE...
echo Press Ctrl+C in this window anytime to stop the bot.
echo.

python main.py --telegram

pause
