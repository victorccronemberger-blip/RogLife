$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$OutputDir = Join-Path $ProjectRoot "dist-local"
$Executable = Join-Path $OutputDir "QMoney.exe"
$Pending = Join-Path $OutputDir "QMoney.next.exe"
$Built = Join-Path $ProjectRoot "build-modular2\QMoney.exe"
$Active = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $Executable)
} | Select-Object -First 1
if ($Active) {
    Write-Output "O QMoney ja esta aberto. Ao encerrar com seguranca, execute este atalho novamente para aplicar a atualizacao local."
    exit 0
}
if (-not (Test-Path -LiteralPath $Executable)) {
    throw 'Distribuicao local ausente. Execute scripts\build_local.ps1 para instalar o aplicativo e as bibliotecas Qt.'
}
# O motor da campanha (moneymin, Python) roda direto do codigo-fonte desta
# copia; alteracoes em .py valem na proxima abertura, sem recompilar. Aqui so
# promovemos um QMoney.exe compilado manualmente, quando ele for mais novo.
$Candidate = $null
if (Test-Path -LiteralPath $Pending) { $Candidate = Get-Item -LiteralPath $Pending }
if (Test-Path -LiteralPath $Built) {
    $BuildFile = Get-Item -LiteralPath $Built
    $InstalledFile = Get-Item -LiteralPath $Executable
    if ($BuildFile.LastWriteTimeUtc -gt $InstalledFile.LastWriteTimeUtc -and
        (-not $Candidate -or $BuildFile.LastWriteTimeUtc -gt $Candidate.LastWriteTimeUtc)) {
        $Candidate = $BuildFile
    }
}
if ($Candidate) {
    $InstalledHash = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash
    $CandidateHash = (Get-FileHash -LiteralPath $Candidate.FullName -Algorithm SHA256).Hash
    if ($InstalledHash -ne $CandidateHash) {
        Copy-Item -LiteralPath $Executable -Destination "$OutputDir\QMoney.previous.exe" -Force
        Copy-Item -LiteralPath $Candidate.FullName -Destination $Executable -Force
        Write-Output "Atualizacao local aplicada: $($Candidate.FullName)"
    }
}
if ($Candidate -and (Test-Path -LiteralPath $Pending)) {
    # Delete only the exact staged file inside the known distribution directory.
    Remove-Item -LiteralPath $Pending
}
Start-Process -FilePath $Executable -WorkingDirectory $OutputDir -WindowStyle Normal