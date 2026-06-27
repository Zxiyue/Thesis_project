# 在 fpa5 根目录运行：powershell -ExecutionPolicy Bypass -File scripts\cleanup_fpa5_safe.ps1
if (!(Test-Path "configs") -or !(Test-Path "scripts")) {
  Write-Host "请在 fpa5 根目录运行本脚本。"
  exit 1
}

Write-Host "[1/4] 备份旧配置到 configs\_archive_old_configs"
New-Item -ItemType Directory -Force -Path "configs\_archive_old_configs" | Out-Null
$oldConfigs = @(
  "mnist_iid.yaml",
  "mnist_noniid.yaml",
  "mnist_iid_distributed_acc.yaml",
  "mnist_iid_realnet.yaml",
  "mnist_noniid_realnet.yaml",
  "mnist_iid_distributed_workers.yaml",
  "mnist_noniid_distributed_workers.yaml"
)
foreach ($f in $oldConfigs) {
  $src = "configs\$f"
  if (Test-Path $src) {
    Move-Item -Force $src "configs\_archive_old_configs\$f"
    Write-Host "  archived $src"
  }
}

Write-Host "[2/4] 删除根目录临时生成文件"
Remove-Item -Force -ErrorAction SilentlyContinue "fpa5_50r_main_configs.zip"
Remove-Item -Force -ErrorAction SilentlyContinue "README_四组50轮主实验配置.md"

Write-Host "[3/4] outputs/data/node_modules/artifacts/cache 不自动删除，上传服务器时排除即可"
Write-Host "[4/4] 当前 configs："
Get-ChildItem -Recurse -File configs | Sort-Object FullName | ForEach-Object { $_.FullName }
