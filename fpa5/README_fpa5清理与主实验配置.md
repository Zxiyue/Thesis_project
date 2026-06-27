# fpa5 清理包：主实验配置与安全清理脚本

本包按当前 fpa5 已跑通的 distributed YAML 风格整理，不再使用先前“experiment/model/dataset.partition”那种新结构。

## 建议保留的 configs

正式主实验只保留四个 50 轮配置：

- configs/mnist_iid_50r_main.yaml
- configs/mnist_noniid_50r_main.yaml
- configs/fashion_mnist_iid_50r_main.yaml
- configs/fashion_mnist_noniid_50r_main.yaml

可选保留两个模板/回退配置：

- configs/mnist_iid_distributed.yaml
- configs/mnist_noniid_distributed.yaml

其余旧配置建议先移动到 configs/_archive_old_configs，不要直接删除。

## 使用方式

在 fpa5 根目录下执行：

```bash
# 1. 先备份旧配置
mkdir -p configs/_archive_old_configs
mv configs/mnist_iid.yaml configs/_archive_old_configs/ 2>/dev/null || true
mv configs/mnist_noniid.yaml configs/_archive_old_configs/ 2>/dev/null || true
mv configs/mnist_iid_distributed_acc.yaml configs/_archive_old_configs/ 2>/dev/null || true
mv configs/mnist_iid_realnet.yaml configs/_archive_old_configs/ 2>/dev/null || true
mv configs/mnist_noniid_realnet.yaml configs/_archive_old_configs/ 2>/dev/null || true
mv configs/mnist_iid_distributed_workers.yaml configs/_archive_old_configs/ 2>/dev/null || true
mv configs/mnist_noniid_distributed_workers.yaml configs/_archive_old_configs/ 2>/dev/null || true

# 2. 复制本包中的四个主实验配置到 fpa5/configs
cp configs/*.yaml /mnt/fpa5/configs/
```

正式 50 轮前，建议先把 rounds 临时改成 5 做 smoke test。

## 注意

- paillier_bits 仍按当前成功配置保持 512，避免 50 轮主实验过慢。论文中应说明这是实验性能参数，不作为实际部署安全强度建议。
- communication.timeout_seconds 设置为 86400，避免 50 轮长任务被控制器超时中断。
- runtime.device 固定为 cpu，符合当前瓶颈主要在 Paillier 与 Pedersen 的判断。
