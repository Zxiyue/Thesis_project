# fpa5 修改版运行说明

## 1. 安装依赖

建议使用虚拟环境：
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
npm install
```
如果后续每次打开新终端，都需要先进入项目目录并激活环境：
```powershell
cd fpa5
.\.venv\Scripts\activate
```
## 2. 运行 5 客户端分布式实验

这是推荐首先运行的版本，用于验证多进程 individual 模式是否正常。

### 终端 1：启动 Hardhat 本地区块链

```powershell
npx hardhat node
```

该终端保持打开，不要关闭。

### 终端 2：部署合约

```powershell
.\.venv\Scripts\activate
npx hardhat run scripts/deploy.js --network localhost
```

成功后通常会生成或更新：

```text
outputs/contract.json
```

### 终端 3：启动 KGC、CS 和客户端实体

```powershell
.\.venv\Scripts\activate
python scripts/start_all_entities.py --config configs/mnist_iid_distributed.yaml --clients 5
```

该命令会自动启动：

```text
KGC: 127.0.0.1:9200
CS:  127.0.0.1:9300
C1:  127.0.0.1:9401
C2:  127.0.0.1:9402
C3:  127.0.0.1:9403
C4:  127.0.0.1:9404
C5:  127.0.0.1:9405
```

### 终端 4：启动实验

```powershell
.\.venv\Scripts\activate
python scripts/start_experiment.py --config configs/mnist_iid_distributed.yaml
```

该命令只向 CS 发送一次 `StartExperiment` 请求。后续第 1 到第 5 轮由 CS 进程内部推进，KGC 和客户端通过 HTTP 通信交互。

### 实验完成后：收集结果

```powershell
python scripts/collect_results.py --config configs/mnist_iid_distributed.yaml
```

### 停止所有实体

```powershell
python scripts/stop_all_entities.py --config configs/mnist_iid_distributed.yaml
```

## 3. 查看关键结果

默认输出目录为：

```text
outputs/mnist_iid_distributed/
```

重点检查：

```text
verify_result.csv
communication_cost.csv
communication_network_only.csv
communication_processing_requests.csv
communication_summary_by_type.csv
runtime_cost.csv
blockchain_cost.csv
crypto_cost.csv
audit_chain.csv
run_trace.json
summary_tables.xlsx
```

### 检查第三方验证结果

```powershell
Import-Csv outputs\mnist_iid_distributed\verify_result.csv
```

正常应看到：

```text
accepted=True
```

### 检查模型同步消息

默认配置中第 3 轮 C2 掉线，第 4 轮重新上线。检查是否有 `ModelSyncReq` 和 `ModelSyncResp`：

```powershell
Import-Csv outputs\mnist_iid_distributed\communication_cost.csv |
  Where-Object { $_.payload_type -in @('ModelSyncReq','ModelSyncResp') } |
  Format-Table round,link,payload_type,client_id,bytes,time_category -AutoSize
```

正常应看到第 4 轮：

```text
C2->CS  ModelSyncReq
CS->C2  ModelSyncResp
```

同时 `run_trace.json` 中应有：

```json
"sync_performed": true,
"sigSyncCS_verified": true
```

## 4. 新增通信统计文件说明

本次修改后，通信统计分成三类文件。

### communication_cost.csv

完整通信记录，保留所有 HTTP 请求，包括纯通信请求和包含处理时间的端到端请求。

新增字段：

```text
includes_processing
time_category
```

其中：

```text
network_transfer：主要表示网络传输请求
end_to_end_with_processing：表示请求耗时包含对端处理过程
experiment_total：表示完整实验总等待过程，例如 StartExperiment
```

### communication_network_only.csv

只保留 `time_category=network_transfer` 的记录。论文中分析纯通信量和通信耗时时，优先使用这个文件。

### communication_processing_requests.csv

保留 `TrainUploadReq`、`StartExperiment` 等包含计算过程的请求。它们不能当作纯网络通信时间，只能解释为端到端请求耗时。

### communication_summary_by_type.csv

按 `payload_type` 汇总通信条数、总字节数、总耗时和总 MB。适合用于论文画柱状图。

## 5. 新增运行时间统计说明

本次修改后，`runtime_cost.csv` 增加了：

```text
client_upload_parallel_wall
start_experiment_total_wall
```

含义如下：

```text
client_upload_parallel_wall：CS 并行触发所有在线客户端训练上传后，等待全部完成的墙钟时间。
start_experiment_total_wall：CS 驱动完整分布式实验的总墙钟时间。
```

分析运行时间时，应区分：

```text
客户端内部累计工作量：client_paillier_encrypt、client_pedersen_commit 等
并行墙钟时间：client_upload_parallel_wall
完整实验时间：start_experiment_total_wall
```

## 6. 运行训练效果增强配置

如果想改善准确率，可以运行新增配置：

```text
configs/mnist_iid_distributed_acc.yaml
```

该配置提高了训练轮数和本地训练强度，运行方式与普通分布式实验一致：

```powershell
python scripts/start_all_entities.py --config configs/mnist_iid_distributed_acc.yaml --clients 5
python scripts/start_experiment.py --config configs/mnist_iid_distributed_acc.yaml
python scripts/collect_results.py --config configs/mnist_iid_distributed_acc.yaml
python scripts/stop_all_entities.py --config configs/mnist_iid_distributed_acc.yaml
```

注意：该配置会明显更慢，适合训练效果实验；如果只是验证系统流程和通信开销，优先使用 `configs/mnist_iid_distributed.yaml`。

## 7. 不使用区块链运行

如果只是调试分布式通信，可以在配置中把：

```yaml
blockchain:
  enabled: true
```

改为：

```yaml
blockchain:
  enabled: false
```

然后可以跳过 Hardhat 启动和合约部署步骤。

## 8. 常见问题

### 端口被占用

先停止旧实体：

```powershell
python scripts/stop_all_entities.py --config configs/mnist_iid_distributed.yaml
```

如果仍然占用，可以手动结束 Python 进程：

```powershell
taskkill /IM python.exe /F
```

### 没有 blockchain_cost.csv 或链上报错

确认已经执行：

```powershell
npx hardhat node
npx hardhat run scripts/deploy.js --network localhost
```

并确认配置中：

```yaml
blockchain:
  enabled: true
  rpc_url: http://127.0.0.1:8545
```

### StartExperiment 耗时很长

这是正常的。`StartExperiment` 请求会等待 CS 完成整个实验后才返回，所以它代表完整实验端到端耗时，不是纯通信时间。

### TrainUploadReq 耗时几十秒

这是正常的。`TrainUploadReq` 包含客户端本地训练、Paillier 加密、Pedersen 承诺和上传发送过程，不是纯通信时间。纯通信分析应使用 `communication_network_only.csv`。

## 9. 推荐实验顺序

建议按以下顺序进行：

1. 先运行 `configs/mnist_iid_distributed.yaml`，验证 `accepted=True`。
2. 检查 `ModelSyncReq/ModelSyncResp` 和 `sigSyncCS_verified=true`。
3. 检查 `blockchain_cost.csv` 是否有 `InitTx + 5*FinalTx`。
4. 使用 `communication_summary_by_type.csv` 分析通信量。
5. 使用 `runtime_cost.csv` 分析计算瓶颈。
6. 最后再运行 `configs/mnist_iid_distributed_acc.yaml` 观察训练准确率是否提升。
