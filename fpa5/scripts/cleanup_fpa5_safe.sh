#!/usr/bin/env bash
set -euo pipefail

# Run from fpa5 root directory.
if [ ! -d "configs" ] || [ ! -d "scripts" ]; then
  echo "请在 fpa5 根目录运行本脚本。"
  exit 1
fi

echo "[1/4] 备份旧配置到 configs/_archive_old_configs"
mkdir -p configs/_archive_old_configs
for f in   mnist_iid.yaml   mnist_noniid.yaml   mnist_iid_distributed_acc.yaml   mnist_iid_realnet.yaml   mnist_noniid_realnet.yaml   mnist_iid_distributed_workers.yaml   mnist_noniid_distributed_workers.yaml
  do
    if [ -f "configs/$f" ]; then
      mv "configs/$f" "configs/_archive_old_configs/$f"
      echo "  archived configs/$f"
    fi
  done

echo "[2/4] 删除根目录下临时生成文件（只删确定无运行依赖的文件）"
rm -f fpa5_50r_main_configs.zip
rm -f README_四组50轮主实验配置.md
# run_all_50r_main.sh 可保留，也可按需删除；这里先不删除。

echo "[3/4] 提示：outputs 如需清理，建议手动备份后再删"
echo "  可执行：mv outputs outputs_backup_$(date +%Y%m%d_%H%M%S) && mkdir outputs"

echo "[4/4] 清理完成。当前 configs："
find configs -maxdepth 2 -type f | sort
