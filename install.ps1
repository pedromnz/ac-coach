<#
AC Coach installer / instalador

  .\install.ps1 -Version standalone -AcPath "D:\Steam\steamapps\common\assettocorsa"
  .\install.ps1 -Version simhub     -AcPath "D:\Steam\steamapps\common\assettocorsa" -SimHubPath "C:\Program Files (x86)\SimHub"

standalone : copies the in-game app only.            / copia so o app do jogo.
simhub     : copies the app + the "AC Coach Lovely"   / copia o app + o dash "AC Coach Lovely",
             dash, pulls the Lovely Dashboard files     puxa os arquivos do Lovely Dashboard
             from your own install and fixes the        da sua instalacao e corrige o caminho
             live.txt path inside the dash.             do live.txt dentro do dash.

Your saved laps (ac_coach\data\*.json) are never overwritten.
Suas voltas salvas (ac_coach\data\*.json) nunca sao sobrescritas.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("standalone", "simhub")][string]$Version,
    [string]$AcPath = "C:\Program Files (x86)\Steam\steamapps\common\assettocorsa",
    [string]$SimHubPath = "C:\Program Files (x86)\SimHub"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $AcPath "acs.exe"))) {
    throw "acs.exe not found in '$AcPath'. Pass -AcPath <Assetto Corsa folder> / Passe -AcPath <pasta do Assetto Corsa>."
}

# ---- 1. app do jogo -------------------------------------------------------
$src = Join-Path $repo "$Version\ac_coach"
$dst = Join-Path $AcPath "apps\python\ac_coach"
New-Item -ItemType Directory -Force -Path (Join-Path $dst "data") | Out-Null
Get-ChildItem $src -File | Copy-Item -Destination $dst -Force
Write-Host "[ok] app -> $dst"

if ($Version -eq "standalone") {
    Write-Host "Done. Enable 'AC Coach' in Content Manager > Settings > Assetto Corsa > Python apps."
    Write-Host "Pronto. Ative 'AC Coach' em Content Manager > Configuracoes > Assetto Corsa > Python apps."
    return
}

# ---- 2. dash do SimHub ----------------------------------------------------
$templates = Join-Path $SimHubPath "DashTemplates"
if (-not (Test-Path $templates)) { throw "SimHub DashTemplates not found: $templates" }
$lovely = Join-Path $templates "Lovely Dashboard"
$dash = Join-Path $templates "AC Coach Lovely"
New-Item -ItemType Directory -Force -Path (Join-Path $dash "JavascriptExtensions") | Out-Null
Copy-Item (Join-Path $repo "simhub\dash\AC Coach Lovely\*") $dash -Recurse -Force
Write-Host "[ok] dash -> $dash"

# widgets, fontes e scripts do Lovely: vem da SUA instalacao (nao sao redistribuidos aqui)
if (Test-Path $lovely) {
    foreach ($p in "Central.djson*", "Environment.djson*", "Fuel.djson*", "Session.djson*") {
        Get-ChildItem $lovely -Filter $p -File -ErrorAction SilentlyContinue | Copy-Item -Destination $dash -Force
    }
    foreach ($d in "_SHFonts", "JavascriptExtensions") {
        $from = Join-Path $lovely $d
        if (Test-Path $from) {
            New-Item -ItemType Directory -Force -Path (Join-Path $dash $d) | Out-Null
            Copy-Item (Join-Path $from "*") (Join-Path $dash $d) -Recurse -Force
        }
    }
    Write-Host "[ok] Lovely Dashboard files copied from $lovely"
} else {
    Write-Warning "Lovely Dashboard not found in $lovely - install it first (see README), then run this again."
    Write-Warning "Lovely Dashboard nao encontrado em $lovely - instale primeiro (ver README) e rode de novo."
}

# caminho do live.txt dentro do dash (texto JSON com JS dentro: cada '\' vira '\\\\')
$djson = Join-Path $dash "AC Coach Lovely.djson"
$old = 'E:\\\\SteamLibrary\\\\steamapps\\\\common\\\\assettocorsa\\\\apps\\\\python\\\\ac_coach\\\\live.txt'
$live = (Join-Path $dst "live.txt")
$new = $live.Replace('\', '\\\\')
$text = [IO.File]::ReadAllText($djson)
if ($text.Contains($old)) {
    [IO.File]::WriteAllText($djson, $text.Replace($old, $new), (New-Object Text.UTF8Encoding $false))
    Write-Host "[ok] dash now reads $live"
} else {
    Write-Warning "live.txt path not found in the dash - check the 'acCoach' variable in Dash Studio."
}

Write-Host ""
Write-Host "Done. Restart SimHub, open 'AC Coach Lovely' and enable 'AC Coach' in Content Manager."
Write-Host "Pronto. Reinicie o SimHub, abra o 'AC Coach Lovely' e ative o 'AC Coach' no Content Manager."
