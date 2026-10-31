param(
  [string]$McpHome = "$env:USERPROFILE\.codex\mcp\kugo-mcp",
  [string]$WebViewRuntime = "$PSScriptRoot\..\glass-widget\src-tauri\target\release\webview2-runtime"
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
$env:PYTHONPATH = "$McpHome\venv\Lib\site-packages"
$node = Join-Path $McpHome 'node\node.exe'
$modules = Join-Path $McpHome 'meting\node_modules'
foreach ($path in @($node, $modules, "$WebViewRuntime\msedgewebview2.exe", "$env:PYTHONPATH\mcp")) {
  if (!(Test-Path -LiteralPath $path)) { throw "Missing build dependency: $path" }
}
if ((& $node --version) -ne 'v24.22.1') { throw 'Update NODE-LICENSE.txt to match the Node.js version before packaging' }
Push-Location $root
try {
  Push-Location glass-widget
  try { npm run build; if ($LASTEXITCODE) { throw 'Widget build failed' } } finally { Pop-Location }
  $assets = @('native_bridge.js', 'index.html', 'style.css', 'app.js', 'kugou-api.js')
  $bridgeArgs = @('--noconfirm','--onedir','--console','--name','kugou-bridge','--distpath',"$root\packaging\build",'--workpath',"$root\packaging\work-bridge",'--specpath',"$root\packaging",'--copy-metadata','mcp','--hidden-import','kugou_native_mcp.installation')
  foreach ($asset in $assets) { $bridgeArgs += @('--add-data', "$root\${asset};.") }
  python -m PyInstaller @bridgeArgs "$root\server.py"
  if ($LASTEXITCODE) { throw 'Bridge build failed' }
  $notices = "$root\packaging\build\python-licenses"
  New-Item -ItemType Directory -Force -Path $notices | Out-Null
  Get-ChildItem -LiteralPath $env:PYTHONPATH -Directory -Filter '*.dist-info' | ForEach-Object {
    $distribution = $_
    Get-ChildItem -LiteralPath $distribution.FullName -Recurse -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE)' } | ForEach-Object {
      $destination = Join-Path $notices $distribution.Name
      New-Item -ItemType Directory -Force -Path $destination | Out-Null
      Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination $_.Name) -Force
    }
  }
  $pythonLicense = python -c "import sys; from pathlib import Path; print(Path(sys.base_prefix) / 'LICENSE.txt')"
  if (!(Test-Path -LiteralPath $pythonLicense)) { throw 'Python license not found' }
  Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $notices 'PYTHON-LICENSE.txt') -Force
  $pyinstallerLicense = python -c "import importlib.metadata as m; print(next(m.distribution('pyinstaller').locate_file(f) for f in m.distribution('pyinstaller').files if str(f).endswith('licenses/COPYING.txt')))"
  if (!(Test-Path -LiteralPath $pyinstallerLicense)) { throw 'PyInstaller license not found' }
  Copy-Item -LiteralPath $pyinstallerLicense -Destination (Join-Path $notices 'PYINSTALLER-LICENSE.txt') -Force
  $outerArgs = @('--noconfirm','--onefile','--windowed','--name','KUGOUPLAY','--distpath',"$root\dist",'--workpath',"$root\packaging\work-launcher",'--specpath',"$root\packaging",'--icon',"$root\glass-widget\src-tauri\icons\icon.ico",'--version-file',"$root\packaging\windows-version.txt")
  $items = @(
    @("$root\glass-widget\src-tauri\target\release\KUGOUPLAY.exe", '.'),
    @("$root\packaging\build\kugou-bridge", 'bridge'),
    @($WebViewRuntime, 'webview2-runtime'),
    @($node, 'mcp/node'),
    @($modules, 'mcp/meting/node_modules'),
    @("$root\packaging\NODE-LICENSE.txt", 'licenses'),
    @("$root\glass-widget\src\vendor\THREE-LICENSE.txt", 'licenses'),
    @("$root\glass-widget\src-tauri\native\liquid-glass\LICENSE", 'licenses/liquid-glass'),
    @("$root\packaging\KUGO-MCP-LICENSE.txt", 'licenses/kugo-mcp'),
    @($notices, 'licenses/python-dependencies')
  )
  foreach ($item in $items) { $outerArgs += @('--add-data', "$($item[0]);$($item[1])") }
  python -m PyInstaller @outerArgs "$root\packaging\launcher.py"
  if ($LASTEXITCODE) { throw 'Portable EXE build failed' }
} finally { Pop-Location }
