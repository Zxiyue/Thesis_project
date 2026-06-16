import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fl_audit.crypto.signature import generate_keypair, sign_obj, verify_obj
from fl_audit.transactions import FinalTx


def test_finaltx_comagg_payload_type_consistency():
    kp = generate_keypair()
    payload = {
        "r": 1,
        "alpha": "0x" + "11" * 32,
        "rootUp": "0x" + "22" * 32,
        "ComAgg": str(123456789012345678901234567890),
        "ComAggHash": "0x" + "33" * 32,
        "modelHashR": "0x" + "44" * 32,
        "modelHashNext": "0x" + "55" * 32,
        "auditRoot": "0x" + "66" * 32,
    }
    sig = sign_obj(kp.private_key, payload)
    tx = FinalTx(
        r=payload["r"],
        alpha=payload["alpha"],
        rootUp=payload["rootUp"],
        ComAgg=int(payload["ComAgg"]),  # simulate old int construction path
        ComAggHash=payload["ComAggHash"],
        modelHashR=payload["modelHashR"],
        modelHashNext=payload["modelHashNext"],
        auditRoot=payload["auditRoot"],
        sigFinal=sig,
    )
    assert isinstance(tx.payload()["ComAgg"], str)
    assert tx.payload() == payload
    assert verify_obj(kp.public_pem, tx.sigFinal, tx.payload())


if __name__ == "__main__":
    test_finaltx_comagg_payload_type_consistency()
    print("FinalTx payload consistency test passed")
