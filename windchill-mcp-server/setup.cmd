@echo off
rem One-command setup of the Windchill RV&S MCP server. Run "setup.cmd --help" for options.
setlocal
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js was not found on PATH.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo Install Node.js 18 or newer from https://nodejs.org and run setup.cmd again.
    exit /b 1
  )
  echo Installing Node.js LTS with winget...
  winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
  echo.
  echo Node.js installed. Open a NEW terminal window so PATH is refreshed, then run setup.cmd again.
  exit /b 1
)
node "%~dp0scripts\setup.mjs" %*
exit /b %errorlevel%
