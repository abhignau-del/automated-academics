@echo off
title Stop Automated Academics
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-app.ps1"
