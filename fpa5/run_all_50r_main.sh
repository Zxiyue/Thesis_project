#!/usr/bin/env bash
set -euo pipefail

# 在 /mnt/fpa5 项目根目录执行。
# 前提：Hardhat node 已在另一个 tmux 窗口运行，并已激活 Python 环境。

CONFIGS=(
  "configs/mnist_iid_50r_main.yaml"
  "configs/mnist_noniid_50r_main.yaml"
  "configs/fashion_mnist_iid_50r_main.yaml"
  "configs/fashion_mnist_noniid_50r_main.yaml"
)

NAMES=(
  "mnist_iid_50r_main"
  "mnist_noniid_50r_main"
  "fashion_mnist_iid_50r_main"
  "fashion_mnist_noniid_50r_main"
)

for idx in "${!CONFIGS[@]}"; do
  cfg="${CONFIGS[$idx]}"
  name="${NAMES[$idx]}"

  echo "========== Running ${name} =========="
  npx hardhat run scripts/deploy.js --network localhost
  python scripts/start_all_entities.py --config "${cfg}" --clients 5
  python scripts/start_experiment.py --config "${cfg}"
  python scripts/collect_results.py --config "${cfg}"
  python scripts/stop_all_entities.py --config "${cfg}" || true
  zip -r "/mnt/${name}_results.zip" "outputs/${name}"
  echo "========== Finished ${name} =========="

  sleep 5
done
