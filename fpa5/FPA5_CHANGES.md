# FPA5 changes

This package extends the FPA4 distributed prototype with protocol-consistency fixes and runnable multi-process improvements:

- KGC now stores and verifies the CS public key for `ModelMsg` / `sigModel`.
- CS no longer sends `xAgg` to KGC in distributed final confirmation. KGC derives the encoded aggregate update from `W_{r+1}-W_r` and verifies `PedCom(xModel;0)==ComAgg`.
- KGC no longer discloses the full Paillier private key to CS in distributed setup. CS receives only the Paillier public key and reconstructs the decryption parameter from threshold shares.
- Client-side `ModelSyncResp` handling checks that the response model hash equals the requested target hash before accepting a synchronized model.
- CS triggers active clients in parallel with `ThreadPoolExecutor`; `TrainUploadReq` log rows are marked with `includes_processing=1` because they include client-side compute time.
- Worker-pool routing is connected through `state_store.client_url/client_endpoint_info`, so logical clients can be routed to `ClientWorker` processes.
- `start_all_entities.py` supports both `individual` and `worker_pool` modes.

The original single-process `run.py` remains available.
