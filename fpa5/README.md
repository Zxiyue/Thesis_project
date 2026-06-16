# FL Public Audit V3: CNN + Communication Profiling

This version upgrades the experiment from a linear MNIST classifier to a compact CNN and adds logical communication-cost profiling.

## Main changes

- `model.name: tiny_cnn` by default. Available options: `logistic_regression`, `tiny_cnn`, `small_cnn`.
- `tiny_cnn` parameter dimension is 6794, which is smaller than the linear baseline dimension 7850.
- Client-side timing remains fine-grained: local training, quantization, Paillier encryption, Pedersen commitment, signing, packing.
- New `communication_cost.csv` records logical Client/CS/KGC/Blockchain communication payload bytes and estimated communication time.
- Optional parallel client upload generation is controlled by:

```yaml
runtime:
  parallel_clients: true
  max_client_workers: 5
```

## Communication model

The project still runs on one machine by default, but communication is no longer ignored. For every logical message, the program records:

- link, e.g., `C1->CS`, `KGC->CS`, `CS->C1`
- payload type, e.g., `UpMsg`, `AggMsg`, `ModelBroadcast`
- payload bytes
- estimated transfer time

The estimate uses:

```text
time = latency_ms / 1000 + bytes * 8 / (bandwidth_mbps * 1e6)
```

The default config does not sleep for the estimated delay. To emulate network delay, set:

```yaml
communication:
  apply_delay: true
```

## Run

No blockchain:

```bash
python run.py --config configs/mnist_noniid.yaml --no-blockchain
```

With Hardhat blockchain:

```bash
npx hardhat node
npx hardhat run scripts/deploy.js --network localhost
python run.py --config configs/mnist_noniid.yaml
```

## Outputs

- `metrics_round.csv`: test loss/accuracy by round
- `runtime_cost.csv`: fine-grained runtime cost
- `communication_cost.csv`: logical communication bytes and estimated latency
- `crypto_cost.csv`: crypto operation counts
- `blockchain_cost.csv`: transaction hash, gas, block number, latency
- `audit_chain.csv`: alpha, rootUp, ComAggHash, model hashes, audit roots
- `verify_result.csv`: third-party audit verification result
- `summary_tables.xlsx`: all tables in one workbook

## Real network communication mode

V4 supports measuring real TCP communication time for each logical protocol message. In this mode, the computation can still be orchestrated by `run.py`, but every logical message such as `MaskMsg`, `UpMsg`, `DropMsg`, `AggMsg`, `ShareMsg`, `ModelMsg`, and `ModelBroadcast` is transmitted through a real TCP socket to the configured receiver endpoint. The measured wall-clock time is written to `communication_cost.csv` as `actual_seconds`.

### Local single-machine real socket test

Open one terminal and start endpoints for CS, KGC and clients:

```powershell
python scripts/start_network_endpoints.py --clients 5
```

Then run the real-network experiment in another terminal:

```powershell
python run.py --config configs/mnist_noniid_realnet.yaml --no-blockchain
```

For full blockchain + real network measurement, keep the network endpoints running, start Hardhat in another terminal, deploy the contract, and run without `--no-blockchain`:

```powershell
npx hardhat node
```

```powershell
npx hardhat run scripts/deploy.js --network localhost
python run.py --config configs/mnist_noniid_realnet.yaml
```

### Multi-machine use

Start one endpoint on each target machine. For example, on the CS machine:

```powershell
python scripts/network_endpoint.py --name CS --host 0.0.0.0 --port 9101
```

On the KGC machine:

```powershell
python scripts/network_endpoint.py --name KGC --host 0.0.0.0 --port 9102
```

On client machine C1:

```powershell
python scripts/network_endpoint.py --name C1 --host 0.0.0.0 --port 9111
```

Then edit the `communication.endpoints` section in the YAML config so each role points to the actual IP address of its machine.

### Output fields

`communication_cost.csv` contains:

- `link`: logical source and destination, such as `C1->CS`.
- `payload_type`: protocol message type.
- `bytes`: transmitted payload size.
- `estimated_seconds`: estimated time from the bandwidth/latency model.
- `actual_seconds`: measured TCP transmission time.
- `status`: `real_socket` when real TCP measurement succeeds.

## Distributed HTTP multi-process mode

V4 also includes an HTTP multi-process experiment mode. KGC, CS and each
client run as separate Python processes; CS is the only round driver after it
receives `StartExperiment`.

Start all entities:

```powershell
python scripts/start_all_entities.py --config configs/mnist_iid_distributed.yaml --clients 5
```

Run the experiment:

```powershell
python scripts/start_experiment.py --config configs/mnist_iid_distributed.yaml
```

Collect merged outputs:

```powershell
python scripts/collect_results.py --config configs/mnist_iid_distributed.yaml
```

Stop all entity processes:

```powershell
python scripts/stop_all_entities.py --config configs/mnist_iid_distributed.yaml
```

The distributed output directory contains the same audit tables as the
single-process mode, plus per-entity logs under `logs/` and structured JSONL
events under `entity_logs/`.

For the default distributed scenario, C2 drops in round 3 and rejoins in round
4. Check synchronization with:

```powershell
Import-Csv outputs\mnist_iid_distributed\communication_cost.csv |
  Where-Object { $_.payload_type -in @('ModelSyncReq','ModelSyncResp') } |
  Format-Table round,link,payload_type,client_id,bytes,status -AutoSize
```

Worker-pool scaffolding is available through:

```powershell
python scripts/start_client_workers.py --clients 100 --clients-per-worker 20 --base-port 9500 --config configs/mnist_iid_distributed_workers.yaml
```
