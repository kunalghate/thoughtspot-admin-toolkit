# ThoughtSpot Admin Toolkit — one-line installer for Windows.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.ps1 | iex"
#
# Installs uv (a self-contained package manager) if it is not already present,
# then installs the toolkit into its own isolated environment. uv downloads a
# managed Python if the machine does not already have a suitable one, so there
# is no prerequisite beyond PowerShell.
#
# The toolkit is installed from the wheel attached to the latest GitHub Release
# rather than from PyPI. Cut a release with `make release-github v=0.1.0`.
#
# Re-running this script upgrades an existing install.

$ErrorActionPreference = "Stop"

$Repo          = "kunalghate/thoughtspot-admin-toolkit"
$PythonVersion = "3.12"
$UvInstaller   = "https://astral.sh/uv/install.ps1"

# uv installs its own binaries here, and puts tool shims here too.
$UvBinDir = Join-Path $env:USERPROFILE ".local\bin"

function Resolve-Uv {
    # A previous run may have installed uv without the shell having been
    # restarted, so look in uv's install directory as well as on PATH.
    if (Get-Command uv -ErrorAction SilentlyContinue) { return "uv" }
    $local = Join-Path $UvBinDir "uv.exe"
    if (Test-Path $local) { return $local }
    return $null
}

Write-Host ""
Write-Host "Installing ThoughtSpot Admin Toolkit..."
Write-Host ""

# ── Step 1: make sure uv is available ──────────────────────────────────────────

$Uv = Resolve-Uv
if (-not $Uv) {
    Write-Host "Installing uv (the package manager the toolkit installs through)..."
    try {
        Invoke-RestMethod $UvInstaller | Invoke-Expression
    } catch {
        throw "Could not install uv. See https://docs.astral.sh/uv/getting-started/installation/"
    }

    $Uv = Resolve-Uv
    if (-not $Uv) {
        throw "uv installed but could not be found. Open a new PowerShell window and run this script again."
    }
}

# ── Step 2: find the wheel on the latest GitHub Release ────────────────────────

Write-Host "Finding the latest version..."

try {
    $Release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
} catch {
    throw "Could not reach GitHub. Check your network connection and try again."
}

$WheelUrl = $Release.assets |
    Where-Object { $_.name -like "*.whl" } |
    Select-Object -First 1 -ExpandProperty browser_download_url

if (-not $WheelUrl) {
    Write-Host ""
    Write-Host "Could not find an installable release."
    Write-Host ""
    Write-Host "This usually means no release has been published yet. Check:"
    Write-Host "    https://github.com/$Repo/releases"
    Write-Host ""
    throw "No release wheel found for $Repo."
}

# ── Step 3: install the toolkit ────────────────────────────────────────────────

# --python makes uv fetch a managed interpreter when the system has none that
# satisfies requires-python. --force makes a re-run replace the existing
# install rather than no-op, which is what makes this script double as the
# upgrade path.
Write-Host "Installing the toolkit..."
& $Uv tool install --force --python $PythonVersion $WheelUrl | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the toolkit from $WheelUrl"
}

# Adds uv's shim directory to PATH if it is not already there. Failure is not
# fatal — we fall back to telling the user the full path.
& $Uv tool update-shell 2>&1 | Out-Null

# ── Step 4: tell the user exactly what to type next ────────────────────────────

Write-Host ""
Write-Host "Done. The toolkit is installed."
Write-Host ""

if (Get-Command ts-admin-toolkit -ErrorAction SilentlyContinue) {
    Write-Host "To start it, run:"
    Write-Host ""
    Write-Host "    ts-admin-toolkit serve"
} else {
    # The shim directory was added to PATH but this shell has not picked it up
    # yet, so give a command that works right now in this same window.
    Write-Host "To start it, open a NEW PowerShell window and run:"
    Write-Host ""
    Write-Host "    ts-admin-toolkit serve"
    Write-Host ""
    Write-Host "Or, to start it in this window without opening a new one:"
    Write-Host ""
    Write-Host "    $UvBinDir\ts-admin-toolkit.exe serve"
}

Write-Host ""
Write-Host "That opens the app in your browser. Keep the window open while you use it."
Write-Host ""
