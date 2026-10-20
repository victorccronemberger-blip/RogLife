param(
    [switch]$SkipTests,
    [switch]$BuildOnly,
    [string]$ToolchainRoot = '',
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '1.0.63'
)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$WorkspaceRoot = (Resolve-Path "$ProjectRoot\..").Path
function Get-NativeBuildPath([string]$Path) {
    # Existing Windows short names avoid windres/gcc quoting bugs. This is the
    # same directory; no alias is created and profile/data paths stay unchanged.
    if ($Path -match '\s') {
        $FileSystem = New-Object -ComObject Scripting.FileSystemObject
        $ShortPath = $FileSystem.GetFolder($Path).ShortPath
        if ($ShortPath -and $ShortPath -notmatch '\s') { return $ShortPath }
    }
    return $Path
}
$NativeProjectRoot = Get-NativeBuildPath $ProjectRoot
if (-not $ToolchainRoot) { $ToolchainRoot = $env:QMONEY_TOOLCHAIN_ROOT }
if (-not $ToolchainRoot) {
    $ToolchainCandidates = @((Join-Path $ProjectRoot 'tools\sdk'), $ProjectRoot, $WorkspaceRoot, (Join-Path $WorkspaceRoot 'QFar'))
    $ToolchainRoot = $ToolchainCandidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ '.local-tools\6.8.3\mingw_64\bin\qmake.exe')
    } | Select-Object -First 1
}
if (-not $ToolchainRoot) { throw 'Informe -ToolchainRoot ou QMONEY_TOOLCHAIN_ROOT com as ferramentas Qt/MinGW/CMake.' }
$ToolchainRoot = (Resolve-Path -LiteralPath $ToolchainRoot).Path
$NativeToolchainRoot = Get-NativeBuildPath $ToolchainRoot
$QtRoot = Join-Path $NativeToolchainRoot ".local-tools\6.8.3\mingw_64"
$MingwBin = Join-Path $NativeToolchainRoot ".local-tools\Tools\mingw1310_64\bin"
$CMake = Join-Path $NativeToolchainRoot ".local-build-python\cmake\data\bin\cmake.exe"
$CTest = Join-Path $NativeToolchainRoot ".local-build-python\cmake\data\bin\ctest.exe"
$Ninja = Join-Path $NativeToolchainRoot ".local-build-python\bin\ninja.exe"
# Never reuse the copied pre-refactor cache, which names the original checkout.
$BuildDir = Join-Path $ProjectRoot "build-modular2"
$NativeBuildDir = Join-Path $NativeProjectRoot "build-modular2"
$OutputDir = Join-Path $ProjectRoot "dist-local"
$env:PATH = "$MingwBin;$QtRoot\bin;$env:PATH"
$env:QMONEY_VERSION = $Version
$ConfigureOptions = @()
$CachePath = Join-Path $BuildDir 'CMakeCache.txt'
if (Test-Path -LiteralPath $CachePath) {
    $Cache = Get-Content -LiteralPath $CachePath -Raw
    $HomeMatch = [regex]::Match($Cache, '(?m)^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$')
    $CompilerMatch = [regex]::Match($Cache, '(?m)^CMAKE_CXX_COMPILER:(?:FILEPATH|STRING)=(.+)$')
    $ExpectedHome = [IO.Path]::GetFullPath((Join-Path $NativeProjectRoot 'desktop')).Replace('\', '/')
    $ExpectedCompiler = [IO.Path]::GetFullPath((Join-Path $MingwBin 'g++.exe')).Replace('\', '/')
    if (-not $HomeMatch.Success -or $HomeMatch.Groups[1].Value.Trim() -ine $ExpectedHome -or
        -not $CompilerMatch.Success -or $CompilerMatch.Groups[1].Value.Trim() -ine $ExpectedCompiler) {
        # A copied checkout or moved SDK cannot reuse another compiler's cache.
        # CMake regenerates only its generated cache inside build-modular2.
        $ConfigureOptions += '--fresh'
    }
}
& $CMake @ConfigureOptions -S "$NativeProjectRoot\desktop" -B $NativeBuildDir -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=$QtRoot" "-DCMAKE_MAKE_PROGRAM=$Ninja" "-DCMAKE_CXX_COMPILER=$MingwBin\g++.exe" -DQMONEY_BUILD_LOCAL_TESTS=ON
if ($LASTEXITCODE -ne 0) { throw "Configuracao local falhou." }
& $CMake --build $NativeBuildDir --parallel 4
if ($LASTEXITCODE -ne 0) { throw "Compilacao local falhou." }
if (-not $SkipTests) {
    & $CTest --test-dir $NativeBuildDir --output-on-failure
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
