$ErrorActionPreference = "Stop"

$python = $env:PYTHON
if (-not $python) {
    $bundled = "C:\Users\liram\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) {
        $python = $bundled
    } else {
        $python = "python"
    }
}

$pythonPath = (Get-Command $python).Source
$pyRoot = Split-Path -Parent $pythonPath
$tclRoot = Join-Path $pyRoot "tcl"
$tclLib = Join-Path $tclRoot "tcl8.6"
$tkLib = Join-Path $tclRoot "tk8.6"
$tclDll = Join-Path $pyRoot "DLLs\tcl86t.dll"
$tkDll = Join-Path $pyRoot "DLLs\tk86t.dll"
$env:TCL_LIBRARY = $tclLib
$env:TK_LIBRARY = $tkLib

$ErrorActionPreference = "Continue"
& $python -m pip show pyinstaller *> $null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install pyinstaller
}

& $python -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --windowed `
    --name "DiagramMaker" `
    --additional-hooks-dir "pyinstaller_hooks" `
    --add-data "style.css;." `
    --add-data "$tclLib;_tcl_data" `
    --add-data "$tkLib;_tk_data" `
    --add-binary "$tclDll;." `
    --add-binary "$tkDll;." `
    "main.py"

Write-Host "Executable created at dist\DiagramMaker.exe"
