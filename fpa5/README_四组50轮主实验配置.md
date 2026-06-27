# fpa5 四组 50 轮主实验配置

本配置包包含 4 个主实验 YAML：

1. `configs/mnist_iid_50r_main.yaml`
2. `configs/mnist_noniid_50r_main.yaml`
3. `configs/fashion_mnist_iid_50r_main.yaml`
4. `configs/fashion_mnist_noniid_50r_main.yaml`

## 统一实验参数

- clients: 5
- threshold: 3
- rounds: 50
- model: tiny_cnn
- device: cpu
- local_lr: 0.05
- server_lr: 1.0
- dropout:
  - round 10: client 2 drops
  - round 25: client 4 drops

该掉线配置用于同时验证掉线补偿与掉线客户端重连后的模型同步，同时避免高频掉线严重干扰主收敛曲线。

## 运行前检查

请先确认数据集目录：

```bash
ls /mnt/fpa5/data/MNIST
ls /mnt/fpa5/data/FashionMNIST
```

MatPool / 矩池云常见复制方式：

```bash
cd /mnt/fpa5
mkdir -p data
cp -r /public/torchvision_datasets/MNIST data/
cp -r /public/torchvision_datasets/FashionMNIST data/
```

如果公共目录名称是 `Fashion-MNIST`，则复制后可改名：

```bash
mv /mnt/fpa5/data/Fashion-MNIST /mnt/fpa5/data/FashionMNIST
```

## 推荐执行顺序

每组实验建议先跑 5 轮 smoke test：把对应 YAML 中 `rounds: 50` 临时改为 `rounds: 5`，并把 dropout 临时改为：

```yaml
dropout:
  enabled: true
  schedule:
    3: [2]
  require_model_sync_after_rejoin: true
```

smoke test 通过后，再恢复 50 轮正式实验配置。

## 正式运行命令模板

窗口 1：启动 Hardhat。

```bash
cd /mnt/fpa5
source .venv/bin/activate
npx hardhat node
```

窗口 2：部署合约并启动实体。

```bash
cd /mnt/fpa5
source .venv/bin/activate
npx hardhat run scripts/deploy.js --network localhost
python scripts/start_all_entities.py --config configs/mnist_iid_50r_main.yaml --clients 5
```

窗口 3：启动实验、收集结果、停止实体。

```bash
cd /mnt/fpa5
source .venv/bin/activate
python scripts/start_experiment.py --config configs/mnist_iid_50r_main.yaml
python scripts/collect_results.py --config configs/mnist_iid_50r_main.yaml
python scripts/stop_all_entities.py --config configs/mnist_iid_50r_main.yaml
zip -r /mnt/mnist_iid_50r_main_results.zip outputs/mnist_iid_50r_main
```

其它三组实验只需要替换 config 文件名和 zip 输出名。

## 字段名兼容提醒

由于当前对话没有上传 fpa5 原始 `configs/*.yaml` 模板，本配置采用交接文档中的统一字段组织。如果你的代码模板使用不同字段名，请优先保留原模板结构，只替换以下关键取值：

- 实验名 / 输出目录
- dataset.name
- dataset.partition
- rounds = 50
- clients = 5
- threshold = 3
- model = tiny_cnn
- device = cpu
- local_lr = 0.05
- server_lr = 1.0
- dropout = {10: [2], 25: [4]}
- blockchain enabled = true
- logging / csv output enabled = true

## 每组实验完成后必须检查

结果目录中应至少出现：

- metrics_round.csv
- verify_result.csv
- runtime_cost.csv
- communication_cost.csv
- communication_network_only.csv
- communication_processing_requests.csv
- communication_summary_by_type.csv
- crypto_cost.csv
- blockchain_cost.csv
- audit_chain.csv
- run_trace.json
- summary_tables.xlsx

重点检查：

1. `verify_result.csv`: accepted=True，rounds=50。
2. `blockchain_cost.csv`: 有 InitTx 和 FinalTx gas_used，不能全是 error。
3. `run_trace.json`: 第 11 轮 C2、 第 26 轮 C4 应有模型同步记录，且 sigSyncCS_verified=true。
4. `communication_summary_by_type.csv`: UpMsg 和 AggMsg 应为主要通信量。
5. `runtime_cost.csv`: 应有 client_paillier_encrypt、client_pedersen_commit、client_upload_parallel_wall、start_experiment_total_wall。
