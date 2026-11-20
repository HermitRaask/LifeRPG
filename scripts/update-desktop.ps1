param([string]$PackagePath)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Сначала установите зависимости в .venv.' }
if (Get-Process -Name LifeRPG -ErrorAction SilentlyContinue) {
    throw 'Завершите LifeRPG через трей → Выйти. Затем повторите обновление.'
}
Push-Location -LiteralPath $taskRoot
try {
    if (!$PackagePath) {
        $work = 'build/desktop-' + [guid]::NewGuid().ToString('N')
        & $python -m PyInstaller --noconfirm --workpath $work --distpath dist LifeRPG.spec
        if ($LASTEXITCODE -ne 0) { throw 'Сборка не удалась.' }
        $PackagePath = Join-Path $taskRoot 'dist\LifeRPG'
    }
    $package = (Resolve-Path -LiteralPath $PackagePath).Path
    if (Get-Process -Name LifeRPG -ErrorAction SilentlyContinue) {
        throw 'LifeRPG запущен во время сборки. Закройте его и повторите установку с -PackagePath.'
    }
    & $python scripts/install_desktop.py $package
    if ($LASTEXITCODE -ne 0) { throw 'Установка не завершена. Повторите её после исправления ошибки.' }
    $exe = Join-Path $taskRoot 'LifeRPG.exe'
    $profile = Join-Path $env:LOCALAPPDATA 'LifeRPG'
    $shell = New-Object -ComObject WScript.Shell
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'LifeRPG.lnk'
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exe
    $shortcut.Arguments = '--workspace "' + $profile + '"'
    $shortcut.WorkingDirectory = $taskRoot
    $shortcut.IconLocation = $exe + ',0'
    $shortcut.Save()
    $reopened = $shell.CreateShortcut($shortcutPath)
    if ($reopened.TargetPath -ne $exe -or $reopened.Arguments -ne $shortcut.Arguments) {
        throw 'Проверка ярлыка не прошла.'
    }
    Write-Output 'Обновление установлено. Запускайте LifeRPG обычным ярлыком.'
} finally {
    Pop-Location
}
