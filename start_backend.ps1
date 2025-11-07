# Run from project root: .\start_backend.ps1

if (-not $env:OPENAI_API_KEY -or $env:OPENAI_API_KEY -eq "") {
    Write-Host "ERROR: OPENAI_API_KEY not set in environment." -ForegroundColor Red
    Write-Host ""
    Write-Host "Set for current PowerShell session (temporary):"
    Write-Host "  $env:OPENAI_API_KEY = 'your_openai_api_key_here'"
    Write-Host ""
    Write-Host "Set persistently (PowerShell - user):"
    Write-Host "  setx OPENAI_API_KEY 'your_openai_api_key_here'"
    Write-Host ""
    Write-Host "After setting persistently, restart your shell. Then re-run this script."
    exit 1
}

$activate = Join-Path -Path "." -ChildPath ".venv\Scripts\Activate.ps1"
if (Test-Path $activate) {
    Write-Host "Sourcing virtualenv activation..."
    . $activate
} else {
    Write-Error "Virtualenv activation script not found at $activate. Create .venv or adjust the script."
    exit 1
}
Write-Host "Starting uvicorn backend..."
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
