from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple
import copy
import random
import time
import numpy as np
import torch
import hashlib

from fl_audit.crypto.signature import generate_keypair, sign_obj, verify_obj, ECDSAKeyPair
from fl_audit.crypto import paillier
from fl_audit.crypto import pedersen
from fl_audit.crypto.shamir_recovery import split_secret, reconstruct
from fl_audit.crypto.number import gen_prime
from fl_audit.crypto.merkle import MerkleTree
from fl_audit.encoding import quantize, dequantize, to_list
from fl_audit.model import get_vector, set_vector, model_hash, model_from_serialized_state
from fl_audit.trainer import train_local, weighted_update
from fl_audit.utils.codec import sha256_hex, bytes32_hex, audit_init_root, audit_round_root
from fl_audit.transactions import InitTx, FinalTx


def gen_zero_sum_vectors(client_ids: List[int], dim: int, bound: int, rng: random.Random) -> Dict[int, np.ndarray]:
    out = {}
    running = np.zeros(dim, dtype=np.int64)
    for cid in client_ids[:-1]:
        v = np.array([rng.randint(-bound, bound) for _ in range(dim)], dtype=np.int64)
        out[cid] = v
        running += v
    out[client_ids[-1]] = -running
    return out


def gen_zero_sum_scalars(client_ids: List[int], q: int, rng: random.Random) -> Dict[int, int]:
    out = {}
    running = 0
    for cid in client_ids[:-1]:
        v = rng.randrange(0, q)
        out[cid] = v
        running = (running + v) % q
    out[client_ids[-1]] = (-running) % q
    return out


def model_sync_signed_payload(r: int, client_id: int, model_hash_value: str, model_bytes: bytes) -> Dict[str, Any]:
    """Build the canonical payload signed by CS for ModelSyncResp.

    The signature binds the round, target client, model hash, serialized model
    length, and serialized model bytes hash. The full model bytes are not placed
    directly in the signature payload to keep signing input small and stable.
    """
    return {
        "r": int(r),
        "client_id": int(client_id),
        "modelHash": str(model_hash_value),
        "modelBytesLength": len(model_bytes),
        "modelBytesSha256": hashlib.sha256(model_bytes).hexdigest(),
    }


@dataclass
class Client:
    cid: int
    keypair: ECDSAKeyPair = field(default_factory=generate_keypair)
    share: int | None = None
    share_field: int | None = None
    last_receipt: Dict[str, Any] | None = None
    last_merkle_path: list | None = None
    local_model: Any | None = None
    local_model_hash: str | None = None

    @property
    def public_key(self) -> str:
        return self.keypair.public_pem

    def receive_model_broadcast(self, model, model_hash_value: str) -> None:
        observed_hash = model_hash(model)
        if observed_hash != model_hash_value:
            raise RuntimeError(
                f"client C{self.cid} rejected ModelBroadcast: H(W)={observed_hash} != {model_hash_value}"
            )
        self.local_model = copy.deepcopy(model).cpu()
        self.local_model_hash = model_hash_value

    def needs_model_sync(self, model_hash_r: str) -> bool:
        return self.local_model is None or self.local_model_hash != model_hash_r

    def make_model_sync_request(self, r: int, target_model_hash: str) -> Dict[str, Any]:
        return {
            "r": r,
            "client_id": self.cid,
            "local_model_hash": self.local_model_hash,
            "target_model_hash": target_model_hash,
        }

    def sync_model_from_server(self, response: Dict[str, Any], model_template=None, cs_public_key: str | None = None) -> None:
        target_hash = str(response["modelHash"])
        model_bytes = response["modelBytes"]

        if isinstance(model_bytes, str):
            model_bytes = bytes.fromhex(model_bytes)

        expected_len = int(response.get("modelBytesLength", len(model_bytes)))
        expected_sha = str(response.get("modelBytesSha256", hashlib.sha256(model_bytes).hexdigest()))

        if len(model_bytes) != expected_len:
            raise RuntimeError(
                f"client C{self.cid} rejected ModelSyncResp: modelBytes length mismatch, "
                f"got {len(model_bytes)}, expected {expected_len}"
            )

        observed_sha = hashlib.sha256(model_bytes).hexdigest()
        if observed_sha != expected_sha:
            raise RuntimeError(
                f"client C{self.cid} rejected ModelSyncResp: modelBytes sha256 mismatch"
            )

        if cs_public_key is not None:
            if "sigSyncCS" not in response:
                raise RuntimeError(f"client C{self.cid} rejected ModelSyncResp: missing sigSyncCS")

            signed_payload = model_sync_signed_payload(
                int(response["r"]),
                int(response["client_id"]),
                target_hash,
                model_bytes,
            )

            if not verify_obj(cs_public_key, str(response["sigSyncCS"]), signed_payload):
                raise RuntimeError(f"client C{self.cid} rejected ModelSyncResp: invalid CS signature")

        template = model_template if model_template is not None else self.local_model
        if template is None:
            raise RuntimeError(f"client C{self.cid} cannot sync without a model template")

        synced_model = model_from_serialized_state(template, model_bytes)
        observed_hash = model_hash(synced_model)

        if observed_hash != target_hash:
            raise RuntimeError(
                f"client C{self.cid} rejected ModelSyncResp: H(W)={observed_hash} != {target_hash}"
            )

        self.local_model = synced_model
        self.local_model_hash = target_hash

    def make_upload(self, r: int, alpha: str, model_hash: str, global_model, loader, total_samples: int, cfg: dict, kgc_mask: np.ndarray, kgc_rho: int, paillier_pub, ped_params, timer=None, device: str = "cpu"):
        if self.local_model_hash != model_hash:
            raise RuntimeError(
                f"client C{self.cid} local model hash {self.local_model_hash} does not match round modelHash {model_hash}"
            )
        def tic():
            if device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.synchronize()
            return time.perf_counter()

        def toc(start: float, stage: str, detail: str = ""):
            if device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.synchronize()
            seconds = time.perf_counter() - start
            local_timings[stage] = seconds
            if timer is not None:
                timer.add(r, stage, seconds, detail=detail, cid=self.cid)

        local_timings: Dict[str, float] = {}
        epochs = int(cfg["federated"]["local_epochs"])
        lr = float(cfg["federated"]["local_lr"])
        scale = int(cfg["encoding"]["scale"])

        t0 = tic()
        weight = len(loader.dataset) / float(total_samples)
        toc(t0, "client_weight_compute", "client aggregation weight")

        t0 = tic()
        local_model, local_loss = train_local(global_model, loader, epochs, lr, device=device)
        toc(t0, "client_local_train", "local SGD training")

        t0 = tic()
        u = weighted_update(global_model, local_model, weight).numpy().astype(np.float64)
        toc(t0, "client_model_delta", "weighted local model delta")

        t0 = tic()
        x = quantize(u, scale)
        toc(t0, "client_quant_encode", "fixed-point quantization and signed encoding")

        t0 = tic()
        x_mask = x + kgc_mask
        toc(t0, "client_masking", "xMask = x + m")

        t0 = tic()
        c_vec = [paillier.encrypt(paillier_pub, int(v)) for v in x_mask.tolist()]
        toc(t0, "client_paillier_encrypt", "coordinate-wise Paillier encryption")

        t0 = tic()
        c_hash = bytes32_hex(c_vec)
        toc(t0, "client_cipher_hash", "hash encrypted vector")

        t0 = tic()
        com = pedersen.commit(ped_params, x_mask.tolist(), kgc_rho)
        toc(t0, "client_pedersen_commit", "Pedersen vector commitment")

        receipt = {
            "r": r,
            "i": self.cid,
            "alpha": alpha,
            "modelHash": model_hash,
            "cHash": c_hash,
            "Com": str(com),
        }

        t0 = tic()
        sig_up = sign_obj(self.keypair.private_key, receipt)
        toc(t0, "client_receipt_sign", "sign upload receipt")

        t0 = tic()
        up_msg = {
            "r": r,
            "i": self.cid,
            "alpha": alpha,
            "C": [str(c) for c in c_vec],
            "cHash": c_hash,
            "Com": str(com),
            "Receipt": receipt,
            "sigUp": sig_up,
        }
        self.last_receipt = receipt
        toc(t0, "client_upload_pack", "build upload message")

        return up_msg, {
            "cid": self.cid,
            "local_loss": local_loss,
            "weight": weight,
            "device": device,
            "u_norm": float(np.linalg.norm(u)),
            "x_l1": int(np.abs(x).sum()),
            "xmask_l1": int(np.abs(x_mask).sum()),
            "timings": local_timings,
        }

    def make_decryption_share(self, r: int, alpha: str, cagg_hash: str):
        if self.share is None:
            raise RuntimeError("client has no Shamir share")
        msg = {"r": r, "j": self.cid, "alpha": alpha, "share": str(self.share), "caggHash": cagg_hash}
        sig = sign_obj(self.keypair.private_key, msg)
        return {"ShareMsg": msg, "sigShare": sig}


@dataclass
class KGC:
    keypair: ECDSAKeyPair = field(default_factory=generate_keypair)
    masks: Dict[Tuple[int,int], np.ndarray] = field(default_factory=dict)
    rhos: Dict[Tuple[int,int], int] = field(default_factory=dict)
    state_cache: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    final_cache: Dict[int, FinalTx] = field(default_factory=dict)

    @property
    def public_key(self) -> str:
        return self.keypair.public_pem

    def setup(self, clients: List[Client], model, cfg: dict):
        dim = int(get_vector(model).numel())
        pkey = paillier.keygen(int(cfg["crypto"]["paillier_bits"]))
        ped_params = pedersen.make_params(dim, int(cfg["crypto"].get("pedersen_prime_bits", 521)))
        # Shamir share Paillier lambda for threshold recovery experiment.
        field = gen_prime(max(pkey.private.lam.bit_length() + 32, 256))
        shares = split_secret(pkey.private.lam, len(clients), int(cfg["federated"]["threshold"]), field)
        for c in clients:
            c.share = shares.shares[c.cid]
            c.share_field = shares.prime
        U = [c.cid for c in clients]
        u_leaves = [bytes32_hex({"i": c.cid, "pk": c.public_key}) for c in clients]
        Uroot = MerkleTree(u_leaves).root
        sys_para = {
            "M": cfg["model"]["name"],
            "d": dim,
            "R": cfg["federated"]["rounds"],
            "eta": cfg["federated"]["server_lr"],
            "s": cfg["encoding"]["scale"],
            "q": cfg["encoding"]["q"],
            "pk_p": {"n": str(pkey.public.n), "g": str(pkey.public.g)},
            "n_u": len(clients),
            "t": cfg["federated"]["threshold"],
            "pp_com": {"p": str(ped_params.p), "q": str(ped_params.q), "h": str(ped_params.h), "g_vec_hash": bytes32_hex(ped_params.g_vec)},
        }
        sys_hash = bytes32_hex(sys_para)
        w0 = get_vector(model).numpy().tolist()
        model_hash0 = bytes32_hex(w0)
        audit_root0 = audit_init_root(sys_hash, Uroot, model_hash0)
        payload = {"sysParaHash": sys_hash, "Uroot": Uroot, "modelHash0": model_hash0, "auditRoot0": audit_root0}
        sig_init = sign_obj(self.keypair.private_key, payload)
        init_tx = InitTx(sys_hash, Uroot, model_hash0, audit_root0, sig_init)
        return {
            "U": U,
            "Uroot": Uroot,
            "sysPara": sys_para,
            "sysParaHash": sys_hash,
            "paillier": pkey,
            "pedersen": ped_params,
            "auditRoot0": audit_root0,
            "modelHash0": model_hash0,
            "initTx": init_tx,
            "shareField": shares.prime,
        }

    def init_round(self, r: int, model_hash: str, U: List[int], Uroot: str, dim: int, ped_params, seed: int):
        alpha = bytes32_hex([r, model_hash, Uroot])
        rng = random.Random(seed + r * 1009)
        masks = gen_zero_sum_vectors(U, dim, bound=50, rng=rng)
        rhos = gen_zero_sum_scalars(U, ped_params.q, rng)
        for cid in U:
            self.masks[(r, cid)] = masks[cid]
            self.rhos[(r, cid)] = rhos[cid]
        self.state_cache[r] = {"alpha": alpha, "modelHash": model_hash, "Uroot": Uroot, "U": list(U)}
        return alpha, masks, rhos

    def verify_uploads_and_root(self, r: int, up_msgs: List[Dict[str, Any]], client_pubkeys: Dict[int, str]):
        valid = []
        for msg in up_msgs:
            cid = int(msg["i"])
            receipt = msg["Receipt"]
            ok = (
                msg["cHash"] == bytes32_hex([int(x) for x in msg["C"]]) and
                str(msg["Com"]) == str(receipt["Com"]) and
                msg["cHash"] == receipt["cHash"] and
                verify_obj(client_pubkeys[cid], msg["sigUp"], receipt)
            )
            if ok:
                valid.append(msg)
        leaves = [bytes32_hex(m["Receipt"]) for m in sorted(valid, key=lambda z: int(z["i"]))]
        tree = MerkleTree(leaves)
        for idx, msg in enumerate(sorted(valid, key=lambda z: int(z["i"]))):
            msg["merklePath"] = tree.proof(idx)
        return valid, tree.root

    def make_compensation(self, r: int, D: List[int], paillier_pub, ped_params):
        dim = len(ped_params.g_vec)
        if D:
            m_drop = sum((self.masks[(r, cid)] for cid in D), np.zeros(dim, dtype=np.int64))
            rho_drop = sum((self.rhos[(r, cid)] for cid in D), 0) % ped_params.q
            cdrop = [paillier.encrypt(paillier_pub, int(v)) for v in m_drop.tolist()]
            com_drop = pedersen.commit(ped_params, m_drop.tolist(), rho_drop)
        else:
            # No-dropout optimization: do not encrypt the zero vector.
            # Paillier aggregation uses multiplication in ciphertext space, so 1 is the identity.
            # Pedersen commitments are multiplied in the group, so 1 is also the identity.
            m_drop = np.zeros(dim, dtype=np.int64)
            rho_drop = 0
            cdrop = [1] * dim
            com_drop = 1
        return m_drop, rho_drop, cdrop, com_drop

    def final_confirm(self, r: int, alpha: str, root_up: str, com_agg: int, model_hash_r: str, model_hash_next: str, prev_audit_root: str):
        com_agg_hash = bytes32_hex(str(com_agg))
        audit_root = audit_round_root(prev_audit_root, r, alpha, root_up, com_agg_hash, model_hash_r, model_hash_next)
        payload = {
            "r": r,
            "alpha": alpha,
            "rootUp": root_up,
            "ComAgg": str(com_agg),
            "ComAggHash": com_agg_hash,
            "modelHashR": model_hash_r,
            "modelHashNext": model_hash_next,
            "auditRoot": audit_root,
        }
        sig = sign_obj(self.keypair.private_key, payload)
        tx = FinalTx(r, alpha, root_up, str(com_agg), payload["ComAggHash"], model_hash_r, model_hash_next, audit_root, sig)
        self.final_cache[r] = tx
        return tx


@dataclass
class ServerCS:
    keypair: ECDSAKeyPair = field(default_factory=generate_keypair)

    @property
    def public_key(self) -> str:
        return self.keypair.public_pem

    def aggregate(self, valid_up_msgs: List[Dict[str, Any]], cdrop: List[int], com_drop: int, paillier_pub, ped_params):
        dim = len(cdrop)
        craw = []
        for j in range(dim):
            vals = [int(msg["C"][j]) for msg in valid_up_msgs]
            craw.append(paillier.add_many(paillier_pub, vals))
        cagg = [paillier.add(paillier_pub, a, b) for a, b in zip(craw, cdrop)]
        com_raw = pedersen.mul(ped_params, [int(msg["Com"]) for msg in valid_up_msgs])
        com_agg = pedersen.mul(ped_params, [com_raw, com_drop])
        return craw, cagg, com_raw, com_agg

    def recover_lambda_and_decrypt(self, share_msgs: List[Dict[str, Any]], client_pubkeys: Dict[int, str], field_prime: int, threshold: int, paillier_keypair, cagg: List[int], r: int, alpha: str):
        shares = []
        for sm in share_msgs:
            msg = sm["ShareMsg"]
            cid = int(msg["j"])
            if verify_obj(client_pubkeys[cid], sm["sigShare"], msg):
                shares.append((cid, int(msg["share"])))
            if len(shares) >= threshold:
                break
        if len(shares) < threshold:
            raise RuntimeError("not enough valid shares")
        lam = reconstruct(shares, field_prime)
        # Reconstruct private key parameter lambda and use Paillier decryption.
        priv = paillier.PaillierPrivateKey(lam=lam, mu=paillier_keypair.private.mu)
        xagg = np.array([paillier.decrypt(paillier_keypair.public, priv, int(c)) for c in cagg], dtype=np.int64)
        return xagg, shares
