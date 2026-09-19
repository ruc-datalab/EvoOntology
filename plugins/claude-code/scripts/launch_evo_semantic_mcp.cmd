@echo off
setlocal EnableExtensions

rem Launcher for the EvoOntology semantic MCP server (Windows).
rem Finds a Python interpreter without requiring `pip install evoontology`,
rem forces UTF-8 stdio regardless of the console code page, and runs the
rem bundled server by absolute path so it works from any session cwd.

set "PLUGIN_ROOT=%~dp0.."
set "SERVER=%PLUGIN_ROOT%\evoontology\runtime\mcp_server.py"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if defined EVO_ONTOLOGY_DEBUG (
  echo --- evo-semantic launch %DATE% %TIME% --- >> "%TEMP%\evo-semantic-launch.log"
  echo cwd=%CD% >> "%TEMP%\evo-semantic-launch.log"
  echo server=%SERVER% >> "%TEMP%\evo-semantic-launch.log"
  set >> "%TEMP%\evo-semantic-launch.log"
)

if not exist "%SERVER%" (
  echo evo-semantic: bundled MCP server not found at %SERVER% 1>&2
  exit /b 66
)

if defined EVO_ONTOLOGY_PYTHON if exist "%EVO_ONTOLOGY_PYTHON%" (
  "%EVO_ONTOLOGY_PYTHON%" "%SERVER%" %*
  exit /b
)

if defined USERPROFILE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%SERVER%" %*
  exit /b
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%SERVER%" %*
  exit /b
)

where python3 >nul 2>&1
if not errorlevel 1 (
  python3 "%SERVER%" %*
  exit /b
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%SERVER%" %*
  exit /b
)

echo evo-semantic: no Python interpreter found. Install Python 3.9+ or set EVO_ONTOLOGY_PYTHON. 1>&2
exit /b 127
