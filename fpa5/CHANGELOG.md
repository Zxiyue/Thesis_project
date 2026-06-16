# Changelog

## V2

- Fixed `FinalTx.payload()` `ComAgg` type inconsistency.
  - KGC signs `ComAgg` as a decimal string.
  - `FinalTx.__post_init__()` now normalizes `ComAgg` to `str`.
  - `FinalTx.payload()` always returns `"ComAgg": str(...)`.
  - Third-party verifier now receives canonical JSON bytes identical to the bytes signed by KGC, so ECDSA verification succeeds.
- Removed a duplicated `com_agg = pedersen.mul(...)` line in `ServerCS.aggregate()`.
- Added `tests/test_finaltx_payload.py` to validate signature/payload consistency.

## V3 CNN + communication profiling
- Added TinyCNN and SmallCNN MNIST models.
- Default configs now use `model.name: tiny_cnn`.
- Added logical communication profiler and `communication_cost.csv`.
- Added optional parallel client upload generation with `runtime.parallel_clients`.
- TimerRecorder is thread-safe.
- Pedersen bases are hash-derived to avoid MemoryError for larger models.

## V4 real network communication

- Added `communication.mode: real_socket`.
- Added TCP endpoints for CS, KGC and each client.
- `communication_cost.csv` now records both estimated and measured socket time:
  - `estimated_seconds`
  - `actual_seconds`
  - `receiver_host`, `receiver_port`
  - `ack_sha256`
  - `status`
- Added `scripts/start_network_endpoints.py` for one-machine multi-endpoint tests.
- Added `scripts/network_endpoint.py` for multi-machine deployment.
- Added configs:
  - `configs/mnist_iid_realnet.yaml`
  - `configs/mnist_noniid_realnet.yaml`

## V5 distributed HTTP experiment

- Added HTTP multi-process KGC, CS and Client entity services.
- Added distributed launch, experiment, collection and stop scripts.
- Added distributed configs for IID, non-IID and worker-pool layouts.
- CS now drives the full round loop after one `StartExperiment` request.
- Distributed clients keep independent `local_model` and `local_model_hash`.
- Rejoining clients perform `ModelSyncReq` / signed `ModelSyncResp` before training.
- `collect_results.py` merges per-entity JSONL communication logs into `communication_cost.csv`.
- Added ClientWorker scaffolding for grouping logical clients behind worker processes.
