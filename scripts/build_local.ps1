param([switch]$SkipTests, [switch]$BuildOnly, [string]$ToolchainRoot = '')
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$WorkspaceRoot = (Resolve-Path "$ProjectRoot\..").Path
if (-not $ToolchainRoot) { $ToolchainRoot = $env:QMONEY_TOOLCHAIN_ROOT }
if (-not $ToolchainRoot) {
    $ToolchainCandidates = @($ProjectRoot, $WorkspaceRoot, (Join-Path $WorkspaceRoot 'QFar'))
    $ToolchainRoot = $ToolchainCandidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ '.local-tools\6.8.3\mingw_64\bin\qmake.exe')
    } | Select-Object -First 1
}
if (-not $ToolchainRoot) { throw 'Informe -ToolchainRoot ou QMONEY_TOOLCHAIN_ROOT com as ferramentas Qt/MinGW/CMake.' }
$ToolchainRoot = (Resolve-Path -LiteralPath $ToolchainRoot).Path
$QtRoot = Join-Path $ToolchainRoot ".local-tools\6.8.3\mingw_64"
$MingwBin = Join-Path $ToolchainRoot ".local-tools\Tools\mingw1310_64\bin"
$CMake = Join-Path $ToolchainRoot ".local-build-python\cmake\data\bin\cmake.exe"
$CTest = Join-Path $ToolchainRoot ".local-build-python\cmake\data\bin\ctest.exe"
$Ninja = Join-Path $ToolchainRoot ".local-build-python\bin\ninja.exe"
# Never reuse the copied pre-refactor cache, which names the original checkout.
$BuildDir = Join-Path $ProjectRoot "build-modular2"
$OutputDir = Join-Path $ProjectRoot "dist-local"
$env:PATH = "$MingwBin;$QtRoot\bin;$env:PATH"
$env:QMONEY_VERSION = "1.0.63"
& $CMake -S "$ProjectRoot\desktop" -B $BuildDir -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=$QtRoot" "-DCMAKE_MAKE_PROGRAM=$Ninja" -DQMONEY_BUILD_LOCAL_TESTS=ON
if ($LASTEXITCODE -ne 0) { throw "Configuracao local falhou." }
& $CMake --build $BuildDir --parallel 4
if ($LASTEXITCODE -ne 0) { throw "Compilacao local falhou." }
if (-not $SkipTests) {
    & $CTest --test-dir $BuildDir --output-on-failure
    if ($LASTEXITCODE -ne 0) { throw "Testes nativos falharam." }
    & "$ProjectRoot\.venv\Scripts\python.exe" "$PSScriptRoot\run_tests.py"
    if ($LASTEXITCODE -ne 0) { throw "Testes Python falharam." }
}
if ($BuildOnly) {
    Write-Output "Build local pronto para validacao: $BuildDir\QMoney.exe"
    return
}
$OutputPrefix = [System.IO.Path]::GetFullPath($OutputDir).TrimEnd('\') + '\'
$Active = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($OutputPrefix, [System.StringComparison]::OrdinalIgnoreCase)
} | Select-Object -First 1
if ($Active) {
    Copy-Item -LiteralPath "$BuildDir\QMoney.exe" -Destination "$OutputDir\QMoney.next.exe" -Force
    Write-Output "Edicao aberta preservada. Atualizacao preparada para a proxima abertura: $OutputDir\QMoney.next.exe"
    return
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Copy-Item -LiteralPath "$BuildDir\QMoney.exe" -Destination $OutputDir -Force
Copy-Item -LiteralPath "$BuildDir\QMoneyUpdater.exe" -Destination $OutputDir -Force
& "$QtRoot\bin\windeployqt.exe" --release --no-translations --no-system-d3d-compiler --no-opengl-sw "$OutputDir\QMoney.exe"
if ($LASTEXITCODE -ne 0) { throw "Distribuicao das bibliotecas Qt falhou." }
Write-Output "Edicao local pronta: $OutputDir\QMoney.exe"
