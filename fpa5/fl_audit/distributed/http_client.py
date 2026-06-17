from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict
from urllib import error as urlerror
from urllib import request
from urllib.parse import urlparse

from fl_audit.distributed.message_codec import canonical_json_bytes
from fl_audit.distributed.state_store import append_jsonl


# Requests in this set are not pure network-transfer measurements. Their
# actual_seconds values include receiver-side protocol work such as the full
# experiment loop, client-side training/encryption, KGC verification, or final
# model checking. They are still useful as end-to-end request latency, but they
# should not be used as pure network communication time in result analysis.
PROCESSING_TYPES = {
    "StartExperiment",
    "TrainUploadReq",
    "SetupReq",
    "RoundInitReq",
    "VerifyUploadsReq",
    "CompensationReq",
    "AggregateReq",
    "ModelMsg",
    "FinalConfirmReq",
}


def classify_time(payload_type: str, includes_processing: bool = False) -> tuple[int, str]:
    """Return (includes_processing_int, time_category) for a communication row."""
    inc = bool(includes_processing) or payload_type in PROCESSING_TYPES
    if payload_type == "StartExperiment":
        return 1, "experiment_total"
    if inc:
        return 1, "end_to_end_with_processing"
    return 0, "network_transfer"


def send_json(
    sender: str,
    receiver: str,
    url: str,
    payload_type: str,
    payload: Dict[str, Any],
    round_id: int,
    client_id: int | str | None = None,
    worker_id: int | str | None = None,
    timeout: float = 300.0,
    log_path: str | None = None,
    includes_processing: bool = False,
    note: str = "",
) -> Dict[str, Any]:
    payload_bytes = canonical_json_bytes(payload)
    digest = hashlib.sha256(payload_bytes).hexdigest()
    req = request.Request(
        url,
        data=payload_bytes,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    start = time.perf_counter()
    status = "http"
    error = ""
    response_obj: Dict[str, Any] = {}
    response_bytes = b""
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            response_bytes = resp.read()
        response_obj = json.loads(response_bytes.decode("utf-8"))
        if response_obj.get("ack_sha256") not in {None, "", digest}:
            raise RuntimeError("receiver acknowledged a different payload hash")
    except urlerror.HTTPError as exc:
        status = "error"
        body = exc.read()
        error = body.decode("utf-8", errors="replace") if body else repr(exc)
        try:
            response_obj = json.loads(error)
        except Exception:
            response_obj = {}
        raise RuntimeError(f"{exc} body={error}")
    except Exception as exc:
        status = "error"
        error = repr(exc)
        raise
    finally:
        end = time.perf_counter()
        parsed = urlparse(url)
        includes_processing_int, time_category = classify_time(payload_type, includes_processing)
        process_seconds = (
            float(response_obj["server_response_time"]) - float(response_obj["server_receive_time"])
            if response_obj.get("server_receive_time") not in {None, ""}
            and response_obj.get("server_response_time") not in {None, ""}
            else ""
        )
        row = {
            "round": int(round_id),
            "sender": sender,
            "receiver": receiver,
            "link": f"{sender}->{receiver}",
            "payload_type": payload_type,
            "messages": 1,
            "client_id": "" if client_id is None else client_id,
            "worker_id": "" if worker_id is None else worker_id,
            "bytes": len(payload_bytes),
            "actual_seconds": end - start,
            "status": status,
            "ack_sha256": response_obj.get("ack_sha256", ""),
            "receiver_host": parsed.hostname or "",
            "receiver_port": parsed.port or "",
            "send_start_time": start,
            "send_end_time": end,
            "server_receive_time": response_obj.get("server_receive_time", ""),
            "server_response_time": response_obj.get("server_response_time", ""),
            "process_seconds": process_seconds,
            "error": error,
            "includes_processing": includes_processing_int,
            "time_category": time_category,
            "note": note,
        }
        if log_path:
            append_jsonl(log_path, row)
            response_payload_type = response_obj.get("_response_payload_type")
            if response_payload_type:
                response_sender = str(response_obj.get("_response_sender", receiver))
                response_receiver = str(response_obj.get("_response_receiver", sender))
                resp_includes_processing, resp_time_category = classify_time(str(response_payload_type), False)
                append_jsonl(log_path, {
                    "round": int(round_id),
                    "sender": response_sender,
                    "receiver": response_receiver,
                    "link": f"{response_sender}->{response_receiver}",
                    "payload_type": response_payload_type,
                    "messages": 1,
                    "client_id": response_obj.get("_response_client_id", "" if client_id is None else client_id),
                    "worker_id": "" if worker_id is None else worker_id,
                    "bytes": len(response_bytes),
                    "actual_seconds": end - start,
                    "status": status,
                    "ack_sha256": hashlib.sha256(response_bytes).hexdigest() if response_bytes else "",
                    "receiver_host": "",
                    "receiver_port": "",
                    "send_start_time": start,
                    "send_end_time": end,
                    "server_receive_time": response_obj.get("server_receive_time", ""),
                    "server_response_time": response_obj.get("server_response_time", ""),
                    "process_seconds": process_seconds,
                    "error": error,
                    "includes_processing": resp_includes_processing,
                    "time_category": resp_time_category,
                    "note": note,
                })
    return response_obj


def get_json(url: str, timeout: float = 300.0) -> Dict[str, Any]:
    with request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
