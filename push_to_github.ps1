<#
.SYNOPSIS
    Lädt dieses Repository zu GitHub hoch (ohne Token im Chatverlauf).

.DESCRIPTION
    Führt Schritt für Schritt aus, was zum Veröffentlichen nötig ist:
      1. Autor-E-Mail auf die GitHub-Noreply-Adresse setzen (nur lokal für diesen Klon)
      2. Remote "origin" einrichten
      3. Push nach GitHub – die Anmeldung übernimmt der Git Credential Manager
         (Browserfenster) oder ein Token, das Sie an der Eingabeaufforderung eingeben.

    Ihre globale Git-Konfiguration wird nicht verändert.

.EXAMPLE
    .\push_to_github.ps1
    .\push_to_github.ps1 -RepoUrl https://github.com/Tetsuo1203/Tsi2.0.git
#>
param(
    [string]$RepoUrl = "https://github.com/Tetsuo1203/Tsi2.0.git",
    [string]$Branch = "main",
    [string]$AuthorName = "Tetsuo1203",
    [string]$AuthorEmail = "146780181+Tetsuo1203@users.noreply.github.com",
    [switch]$SkipAmend
)

$ErrorActionPreference = "Stop"

function Write-Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "    !   $text" -ForegroundColor Yellow }

Push-Location -Path $PSScriptRoot
try {
    Write-Host ""
    Write-Host "  ETF-Timing-Studio  ->  GitHub" -ForegroundColor White
    Write-Host "  Ziel: $RepoUrl" -ForegroundColor DarkGray

    # ------------------------------------------------------------------ git
    Write-Step "Git prüfen"
    $gitVersion = git --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Git ist nicht installiert oder nicht im PATH. Bitte Git für Windows installieren."
    }
    Write-Ok $gitVersion

    if (-not (Test-Path (Join-Path $PSScriptRoot ".git"))) {
        throw "In diesem Ordner liegt kein Git-Repository (.git fehlt)."
    }

    # --------------------------------------------------------------- Autor
    Write-Step "Autor für diesen Klon setzen (globale Konfiguration bleibt unverändert)"
    git config user.name $AuthorName
    git config user.email $AuthorEmail
    Write-Ok "$AuthorName <$AuthorEmail>"

    $commitCount = (git rev-list --count HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $commitCount -or [int]$commitCount -eq 0) {
        throw "Es gibt noch keinen Commit. Bitte zuerst committen."
    }
    Write-Ok "$commitCount Commit(s) vorhanden"

    if (-not $SkipAmend) {
        $currentEmail = (git log -1 --format='%ae')
        if ($currentEmail -ne $AuthorEmail) {
            Write-Step "Letzten Commit auf die GitHub-Adresse umschreiben"
            git commit --amend --no-edit --reset-author | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "git commit --amend ist fehlgeschlagen." }
            Write-Ok "neuer Hash: $(git rev-parse --short HEAD)"
        } else {
            Write-Ok "Autor stimmt bereits"
        }
    }

    # -------------------------------------------------------------- Remote
    Write-Step "Remote 'origin' einrichten"
    $remotes = git remote
    if ($remotes -contains "origin") {
        git remote set-url origin $RepoUrl
        Write-Ok "origin aktualisiert"
    } else {
        git remote add origin $RepoUrl
        Write-Ok "origin angelegt"
    }

    # ---------------------------------------------------------------- Push
    Write-Step "Push nach GitHub (Anmeldung ggf. im Browser)"
    Write-Host "    Hinweis: Beim ersten Mal öffnet sich ein Fenster zur GitHub-Anmeldung." -ForegroundColor DarkGray
    Write-Host "    Alternativ können Sie an der Passwortabfrage ein Token eingeben." -ForegroundColor DarkGray
    Write-Host ""

    $env:GIT_TERMINAL_PROMPT = "1"
    git -c http.sslBackend=openssl push -u origin $Branch

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Warn2 "Der Push ist fehlgeschlagen."
        Write-Host "    Häufigste Ursachen:" -ForegroundColor DarkGray
        Write-Host "      - Repository existiert noch nicht (github.com/new)" -ForegroundColor DarkGray
        Write-Host "      - Anmeldung abgebrochen oder Token ohne Recht 'Contents: write'" -ForegroundColor DarkGray
        Write-Host "      - falscher Kontoname in der URL" -ForegroundColor DarkGray
        throw "Push fehlgeschlagen (Exitcode $LASTEXITCODE)."
    }

    Write-Host ""
    Write-Host "  Fertig." -ForegroundColor Green
    Write-Host "  Repository: $RepoUrl" -ForegroundColor White
    Write-Host "  Commit    : $(git rev-parse --short HEAD)  $(git log -1 --format='%s')" -ForegroundColor DarkGray
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "  FEHLER: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    exit 1
}
finally {
    Pop-Location
}
