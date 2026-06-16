from __future__ import annotations

import argparse
import hashlib
import yaml
import random
from pathlib import Path
import numpy as np
import torch
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from fl_audit.data import make_loaders
from fl_audit.model import make_model, get_vector, set_vector, evaluate, serialize_model_state
from fl_audit.protocol.entities import Client, KGC, ServerCS, model_sync_signed_payload
from fl_audit.protocol.verifier import verify_chain, verify_init
from fl_audit.crypto import paillier, pedersen
from fl_audit.crypto.merkle import MerkleTree
from fl_audit.crypto.signature import verify_obj, sign_obj
from fl_audit.encoding import dequantize, quantize
from fl_audit.utils.codec import bytes32_hex
from fl_audit.utils.timer import TimerRecorder
from fl_audit.utils.export import ensure_dir, save_json, save_csv, save_excel
from fl_audit.blockchain.contract_client import AuditBoardClient
from fl_audit.network import CommunicationProfiler, json_size_bytes, canonical_json_bytes


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/mnist_iid.yaml")
    ap.add_argument("--no-blockchain", action="store_true", help="Run real training/crypto but skip Hardhat transactions")
    return ap.parse_args()


def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_device(cfg: dict) -> str:
    requested = str(cfg.get("runtime", {}).get("device", "auto")).lower()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested.startswith("cuda") and not torch.cuda.is_available():
        print("[WARN] CUDA was requested but is not available. Falling back to CPU.")
        return "cpu"
    return requested


def model_wire_payload(payload_type: str, r: int, model_hash_value: str, model_bytes: bytes, extra: dict | None = None) -> bytes:
    meta = {
        "payload_type": payload_type,
        "r": r,
        "modelHash": model_hash_value,
        "modelBytesLength": len(model_bytes),
        "modelBytesSha256": hashlib.sha256(model_bytes).hexdigest(),
    }
    if extra:
        meta.update(extra)
    meta_bytes = canonical_json_bytes(meta)
    return len(meta_bytes).to_bytes(8, byteorder="big", signed=False) + meta_bytes + model_bytes


def main():
    args = parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    set_seed(int(cfg.get("seed", 42)))
    device = resolve_device(cfg)
    print(f"[runtime] torch_device={device}, cuda_available={torch.cuda.is_available()}")
    out_dir = ensure_dir(cfg.get("output_dir", "outputs/run"))
    timer = TimerRecorder()
    comm = CommunicationProfiler(cfg)

    train_loaders, test_loader, partitions = make_loaders(cfg)
    model = make_model(cfg["model"]["name"])
    dim = int(get_vector(model).numel())
    clients = [Client(cid=i) for i in range(1, int(cfg["federated"]["clients"]) + 1)]
    clients_by_id = {c.cid: c for c in clients}
    kgc = KGC()
    cs = ServerCS()
    client_pubkeys = {c.cid: c.public_key for c in clients}

    with timer.record(0, "setup", "KGC parameters, Paillier, Pedersen, InitTx"):
        setup = kgc.setup(clients, model, cfg)
    init_tx = setup["initTx"]
    pkey = setup["paillier"]
    ped_params = setup["pedersen"]
    U = setup["U"]
    Uroot = setup["Uroot"]

    blockchain_rows = []
    board = None
    if cfg.get("blockchain", {}).get("enabled", True) and not args.no_blockchain:
        board = AuditBoardClient(cfg["blockchain"]["rpc_url"], cfg["blockchain"]["contract_json"])
        with timer.record(0, "blockchain", "submit InitTx"):
            blockchain_rows.append({"round": 0, "tx_type": "InitTx", **board.submit_init(init_tx)})

    init_ok = verify_init(init_tx, kgc.public_key, setup["sysPara"])
    if not init_ok:
        raise RuntimeError("InitTx verification failed")

    metrics_rows = []
    crypto_rows = []
    audit_rows = []
    final_txs = []
    trace = {"initTx": init_tx.__dict__, "rounds": [], "runtime": {"device": device, "parallel_clients": cfg.get("runtime", {}).get("parallel_clients", False)}}

    prev_audit_root = init_tx.auditRoot0
    current_model_hash = init_tx.modelHash0
    for c in clients:
        c.receive_model_broadcast(model, current_model_hash)
    rounds = int(cfg["federated"]["rounds"])
    total_samples = sum(len(loader.dataset) for loader in train_loaders.values())
    dropout_cfg = cfg["federated"].get("dropout", {}) or {}

    loss0, acc0 = evaluate(model, test_loader, device=device)
    metrics_rows.append({"round": 0, "test_loss": loss0, "test_accuracy": acc0, "effective_clients": 0, "dropout_clients": 0})

    for r in range(1, rounds + 1):
        print(f"\n========== ROUND {r} ==========")
        round_trace = {"round": r}
        W_r_vec = get_vector(model).clone().double()
        with timer.record(r, "round_init", "alpha, zero-sum masks, zero-sum rho"):
            alpha, masks, rhos = kgc.init_round(r, current_model_hash, U, Uroot, dim, ped_params, int(cfg.get("seed", 42)))
        round_trace["alpha"] = alpha

        # Logical communication: KGC privately sends MaskMsg to every planned client.
        for cid in U:
            mask_payload = {"r": r, "i": cid, "alpha": alpha, "m": masks[cid].tolist(), "rho": str(rhos[cid])}
            comm.record(r, f"KGC->C{cid}", "MaskMsg", payload=mask_payload, note="private mask and Pedersen randomness")

        drop_ids = set(dropout_cfg.get(r, dropout_cfg.get(str(r), [])) or [])
        up_msgs = []
        client_stats = []
        active_clients = [c for c in clients if c.cid not in drop_ids]
        model_sync_events = []
        current_model_bytes = serialize_model_state(model)
        for c in active_clients:
            old_hash = c.local_model_hash
            sync_performed = c.needs_model_sync(current_model_hash)
            if sync_performed:
                sync_req = c.make_model_sync_request(r, current_model_hash)
                comm.record(
                    r,
                    f"C{c.cid}->CS",
                    "ModelSyncReq",
                    payload=sync_req,
                    note="client requests current model because local hash is stale",
                )
                sync_payload = model_sync_signed_payload(
                    r,
                    c.cid,
                    current_model_hash,
                    current_model_bytes,
                )
                sig_sync_cs = sign_obj(cs.keypair.private_key, sync_payload)
                sync_resp = {
                    "r": r,
                    "client_id": c.cid,
                    "modelHash": current_model_hash,
                    "modelBytesLength": sync_payload["modelBytesLength"],
                    "modelBytesSha256": sync_payload["modelBytesSha256"],
                    "modelBytes": current_model_bytes,
                    "sigSyncCS": sig_sync_cs,
                }
                sync_resp_wire = model_wire_payload(
                    "ModelSyncResp",
                    r,
                    current_model_hash,
                    current_model_bytes,
                    extra={
                        "client_id": c.cid,
                        "modelBytesLength": sync_payload["modelBytesLength"],
                        "modelBytesSha256": sync_payload["modelBytesSha256"],
                        "sigSyncCS": sig_sync_cs,
                    },
                )
                comm.record(
                    r,
                    f"CS->C{c.cid}",
                    "ModelSyncResp",
                    payload=sync_resp_wire,
                    note="signed current round model bytes for stale reconnecting client",
                )
                c.sync_model_from_server(sync_resp, model_template=model, cs_public_key=cs.public_key)
            model_sync_events.append({
                "round": r,
                "client_id": c.cid,
                "old_local_model_hash": old_hash,
                "target_modelHash_r": current_model_hash,
                "sync_performed": sync_performed,
                "sigSyncCS_verified": True if sync_performed else False,
            })
        round_trace["modelSync"] = model_sync_events

        def _client_upload(c: Client):
            return c.make_upload(
                r, alpha, current_model_hash, c.local_model, train_loaders[c.cid], total_samples, cfg,
                masks[c.cid], rhos[c.cid], pkey.public, ped_params, timer=timer, device=device
            )

        parallel_clients = bool(cfg.get("runtime", {}).get("parallel_clients", False))
        max_workers = int(cfg.get("runtime", {}).get("max_client_workers", len(active_clients) or 1))
        if parallel_clients and len(active_clients) > 1:
            with timer.record(r, "client_upload_parallel_wall", "wall-clock time for parallel client upload generation"):
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = {ex.submit(_client_upload, c): c.cid for c in active_clients}
                    for fut in as_completed(futures):
                        msg, stat = fut.result()
                        up_msgs.append(msg)
                        client_stats.append(stat)
        else:
            with timer.record(r, "client_upload_sequential_wall", "wall-clock time for sequential client upload generation"):
                for c in active_clients:
                    msg, stat = _client_upload(c)
                    up_msgs.append(msg)
                    client_stats.append(stat)

        up_msgs = sorted(up_msgs, key=lambda m: int(m["i"]))
        client_stats = sorted(client_stats, key=lambda z: int(z["cid"]))
        for msg in up_msgs:
            cid = int(msg["i"])
            up_size = json_size_bytes(msg)
            comm.record(r, f"C{cid}->CS", "UpMsg", bytes_count=up_size, note="ciphertext vector, commitment, receipt and signature")
            comm.record(r, f"C{cid}->KGC", "UpMsg", bytes_count=up_size, note="copy for KGC-side upload verification")

        with timer.record(r, "kgc_upload_verify", "signatures and rootUp"):
            valid_msgs, root_up = kgc.verify_uploads_and_root(r, up_msgs, client_pubkeys)
        U_r1 = sorted([int(m["i"]) for m in valid_msgs])
        D_r = sorted(list(set(U) - set(U_r1)))
        round_trace["U_r1"] = U_r1
        round_trace["D_r"] = D_r
        round_trace["rootUp"] = root_up

        with timer.record(r, "kgc_compensation", "mDrop, rhoDrop, Cdrop, ComDrop"):
            m_drop, rho_drop, cdrop, com_drop = kgc.make_compensation(r, D_r, pkey.public, ped_params)
        drop_payload = {
            "r": r,
            "alpha": alpha,
            "D": D_r,
            "identityOptimized": len(D_r) == 0,
            "Cdrop": [] if len(D_r) == 0 else [str(c) for c in cdrop],
            "ComDrop": str(com_drop),
        }
        comm.record(r, "KGC->CS", "DropMsg", payload=drop_payload, note="dropout compensation; compact identity when no dropout")

        with timer.record(r, "cs_aggregate", "Craw/Cagg and ComRaw/ComAgg"):
            craw, cagg, com_raw, com_agg = cs.aggregate(valid_msgs, cdrop, com_drop, pkey.public, ped_params)
        cagg_hash = bytes32_hex([str(c) for c in cagg])

        # CS signs two key chain-off messages for accountability.
        agg_payload = {"r": r, "alpha": alpha, "rootUp": root_up, "CaggHash": cagg_hash, "ComAgg": str(com_agg)}
        sig_agg = sign_obj(cs.keypair.private_key, agg_payload)
        if not verify_obj(cs.public_key, sig_agg, agg_payload):
            raise RuntimeError("sigAgg failed")
        agg_msg_payload = {"r": r, "alpha": alpha, "rootUp": root_up, "Cagg": [str(c) for c in cagg], "CaggHash": cagg_hash, "ComAgg": str(com_agg), "sigAgg": sig_agg}
        agg_size = json_size_bytes(agg_msg_payload)
        for cid in U_r1:
            comm.record(r, f"CS->C{cid}", "AggMsg", bytes_count=agg_size, note="aggregate ciphertext vector for partial decryption")
        comm.record(r, "CS->KGC", "AggMsg", bytes_count=agg_size, note="aggregate transcript for KGC checking")

        # Clients produce Shamir shares of Paillier lambda for threshold recovery experiment.
        with timer.record(r, "share_generation", "threshold shares"):
            share_msgs = [c.make_decryption_share(r, alpha, cagg_hash) for c in clients if c.cid not in drop_ids]
        for sm in share_msgs:
            cid = int(sm["ShareMsg"]["j"])
            comm.record(r, f"C{cid}->CS", "ShareMsg", payload=sm, note="threshold decryption share message")

        with timer.record(r, "combine_decrypt", "Shamir reconstruct lambda and decrypt aggregate vector"):
            xagg, used_shares = cs.recover_lambda_and_decrypt(
                share_msgs, client_pubkeys, setup["shareField"], int(cfg["federated"]["threshold"]), pkey, cagg, r, alpha
            )

        with timer.record(r, "cs_open_check", "ComAgg == PedCom(xAgg;0)"):
            com_check = pedersen.commit(ped_params, xagg.tolist(), 0)
            if com_check != com_agg:
                raise RuntimeError("CS open check failed: ComAgg != PedCom(xAgg;0)")

        with timer.record(r, "model_update", "apply decoded aggregate update"):
            update = torch.tensor(dequantize(xagg, int(cfg["encoding"]["scale"])), dtype=torch.float64)
            W_next_vec = W_r_vec + float(cfg["federated"].get("server_lr", 1.0)) * update
            set_vector(model, W_next_vec)
            model_hash_next = bytes32_hex(get_vector(model).numpy().tolist())

        model_payload = {
            "r": r,
            "alpha": alpha,
            "modelHashR": current_model_hash,
            "modelHashNext": model_hash_next,
            "CaggHash": cagg_hash,
            "ComAgg": str(com_agg),
        }
        sig_model = sign_obj(cs.keypair.private_key, model_payload)
        if not verify_obj(cs.public_key, sig_model, model_payload):
            raise RuntimeError("sigModel failed")
        # Logical model publication. ModelSyncResp and ModelBroadcast use the
        # serialized state_dict bytes, while chain records keep only hashes.
        model_msg_meta = dict(model_payload)
        model_msg_meta["sigModel"] = sig_model
        next_model_bytes = serialize_model_state(model)
        model_msg_wire = model_wire_payload(
            "ModelMsg",
            r,
            model_hash_next,
            next_model_bytes,
            extra={"modelHashR": current_model_hash, "CaggHash": cagg_hash, "sigModel": sig_model},
        )
        comm.record(r, "CS->KGC", "ModelMsg", payload=model_msg_wire, note="model hashes plus serialized W_next state_dict")
        for cid in U_r1:
            broadcast_wire = model_wire_payload(
                "ModelBroadcast",
                r,
                model_hash_next,
                next_model_bytes,
                extra={"modelHashR": current_model_hash, "client_id": cid, "sigModel": sig_model},
            )
            comm.record(r, f"CS->C{cid}", "ModelBroadcast", payload=broadcast_wire, note="new global model broadcast with serialized W_next state_dict")
            clients_by_id[cid].receive_model_broadcast(model, model_hash_next)

        with timer.record(r, "kgc_final_model_check", "model diff -> xModel -> PedCom check"):
            u_model = (get_vector(model).double() - W_r_vec) / float(cfg["federated"].get("server_lr", 1.0))
            x_model = quantize(u_model.numpy(), int(cfg["encoding"]["scale"]))
            # Floating precision around .5 can create a one-unit error; report exact rate.
            diff = np.abs(x_model - xagg)
            mismatch = int((diff != 0).sum())
            if mismatch != 0:
                # Use xagg-derived update in this V1 model application, so this should be zero.
                raise RuntimeError(f"KGC model diff encoding mismatch at {mismatch} coordinates")
            if pedersen.commit(ped_params, x_model.tolist(), 0) != com_agg:
                raise RuntimeError("KGC model differential commitment check failed")

        with timer.record(r, "kgc_final_tx", "auditRoot and FinalTx"):
            final_tx = kgc.final_confirm(r, alpha, root_up, com_agg, current_model_hash, model_hash_next, prev_audit_root)
            final_txs.append(final_tx)
            prev_audit_root = final_tx.auditRoot
            current_model_hash = model_hash_next

        if board is not None:
            with timer.record(r, "blockchain", "submit FinalTx"):
                blockchain_rows.append({"round": r, "tx_type": "FinalTx", **board.submit_final(final_tx)})

        loss, acc = evaluate(model, test_loader, device=device)
        metrics_rows.append({
            "round": r,
            "test_loss": loss,
            "test_accuracy": acc,
            "effective_clients": len(U_r1),
            "dropout_clients": len(D_r),
        })
        audit_rows.append({
            "round": r,
            "alpha": alpha,
            "rootUp": root_up,
            "ComAggHash": final_tx.ComAggHash,
            "modelHashR": final_tx.modelHashR,
            "modelHashNext": final_tx.modelHashNext,
            "auditRoot": final_tx.auditRoot,
            "sigFinal_len": len(final_tx.sigFinal),
        })
        compensation_encryptions = dim if len(D_r) > 0 else 0
        compensation_commitments = 1 if len(D_r) > 0 else 0
        crypto_rows.extend([
            {"round": r, "metric": "model_dimension", "value": dim},
            {"round": r, "metric": "model_name", "value": cfg["model"]["name"]},
            {"round": r, "metric": "effective_clients", "value": len(U_r1)},
            {"round": r, "metric": "dropout_clients", "value": len(D_r)},
            {"round": r, "metric": "pedersen_commitments", "value": len(U_r1) + compensation_commitments + 2},
            {"round": r, "metric": "paillier_encryptions", "value": len(U_r1) * dim + compensation_encryptions},
            {"round": r, "metric": "paillier_ciphertexts_aggregated", "value": len(U_r1) * dim},
            {"round": r, "metric": "compensation_encryptions", "value": compensation_encryptions},
            {"round": r, "metric": "compensation_identity_optimized", "value": 1 if len(D_r) == 0 else 0},
        ])
        round_trace.update({
            "rootUp": root_up,
            "ComAggHash": final_tx.ComAggHash,
            "modelHashNext": model_hash_next,
            "auditRoot": final_tx.auditRoot,
            "clientStats": client_stats,
        })
        trace["rounds"].append(round_trace)
        print(f"round={r}, acc={acc:.4f}, effective={len(U_r1)}, dropout={D_r}, auditRoot={final_tx.auditRoot[:18]}...")

    chain_ok = verify_chain(init_tx, final_txs, kgc.public_key)
    print("\nTHIRD-PARTY VERIFY:", "ACCEPT" if chain_ok else "REJECT")
    verify_rows = [{"accepted": chain_ok, "rounds": len(final_txs), "latestAuditRoot": final_txs[-1].auditRoot if final_txs else init_tx.auditRoot0}]

    save_csv(metrics_rows, out_dir / "metrics_round.csv")
    save_csv(timer.rows, out_dir / "runtime_cost.csv")
    save_csv(crypto_rows, out_dir / "crypto_cost.csv")
    save_csv(blockchain_rows, out_dir / "blockchain_cost.csv")
    save_csv(comm.rows, out_dir / "communication_cost.csv")
    save_csv(audit_rows, out_dir / "audit_chain.csv")
    save_csv(verify_rows, out_dir / "verify_result.csv")
    save_excel({
        "metrics_round": metrics_rows,
        "runtime_cost": timer.rows,
        "crypto_cost": crypto_rows,
        "blockchain_cost": blockchain_rows,
        "communication_cost": comm.rows,
        "audit_chain": audit_rows,
        "verify_result": verify_rows,
    }, out_dir / "summary_tables.xlsx")
    if cfg.get("logging", {}).get("save_trace", True):
        save_json(trace, out_dir / "run_trace.json")
    print(f"Outputs saved to: {out_dir}")


if __name__ == "__main__":
    main()
