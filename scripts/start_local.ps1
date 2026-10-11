$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$OutputDir = Join-Path $ProjectRoot "dist-local"
$Executable = Join-Path $OutputDir "QMoney.exe"
$Pending = Join-Path $OutputDir "QMoney.next.exe"
$Active = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Path, $Executable)
} | Select-Object -First 1
if ($Active) {
    Write-Output "O QMoney ja esta aberto. Ao encerrar com seguranca, execute este atalho novamente para aplicar a atualizacao local."
    exit 0
}
if (Test-Path -LiteralPath $Pending) {
    Copy-Item -LiteralPath $Executable -Destination "$OutputDir\QMoney.previous.exe" -Force
    Copy-Item -LiteralPath $Pending -Destination $Executable -Force
    # Delete only the exact staged file inside the known distribution directory.
    Remove-Item -LiteralPath $Pending
}
Start-Process -FilePath $Executable -WorkingDirectory $OutputDir -WindowStyle Normal
