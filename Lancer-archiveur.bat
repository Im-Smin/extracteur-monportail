@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Archiveur monPortail

where python >nul 2>nul
if errorlevel 1 (
  echo Python n'est pas installe. Installe Python 3.12 ou plus recent depuis https://www.python.org/downloads/
  echo ^(coche "Add python.exe to PATH" pendant l'installation^), puis relance ce fichier.
  pause
  exit /b 1
)

if not exist ".installe-archiveur" (
  echo Premiere utilisation : installation des dependances ^(1 a 3 minutes^)...
  python -m pip install --quiet --disable-pip-version-check -e . || goto erreur
  python -m playwright install chromium || goto erreur
  echo ok> ".installe-archiveur"
)

python -m monportail %*
echo.
pause
exit /b 0

:erreur
echo.
echo L'installation a echoue. Copie le message ci-dessus pour obtenir de l'aide.
pause
exit /b 1
