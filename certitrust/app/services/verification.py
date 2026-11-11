import json
import base64
import zlib
from app.db import get_db
from app.crypto.canonicalize import canonicalize
from app.crypto.merkle import sha256_hex, build_merkle_tree
from app.crypto.keys import verify_signature

def verify_from_payload(qr_payload: str, printed_fields: dict = None):
    try:
        # Handle compressed format (CT1:)
        if qr_payload.startswith("CT1:"):
            compressed = base64.b64decode(qr_payload[4:])
            json_str = zlib.decompress(compressed).decode('utf-8')
            data = json.loads(json_str)
        else:
            # Backward compatible with old uncompressed QR
            data = json.loads(qr_payload)
    except Exception as e:
        return {"valid": False, "error": f"Invalid QR payload: {str(e)}"}

    inst_code = data.get("inst")
    fields = data.get("fields")
    signature = data.get("sig")

    if not inst_code or not fields or not signature:
        return {"valid": False, "error": "Missing inst / fields / sig in QR"}

    # 1. Get institution public key
    db = get_db()
    inst = db.execute(
        "SELECT public_key_pem FROM institutions WHERE code = ?", (inst_code,)
    ).fetchone()

    if not inst or not inst["public_key_pem"]:
        return {"valid": False, "error": f"Unknown institution: {inst_code}"}

    # 2. Rebuild ordered leaves (same order as issuance)
    ordered_items = list(fields.items())
    leaf_hashes = []
    for name, value in ordered_items:
        canon = canonicalize(name, value)
        leaf_hashes.append(sha256_hex(canon))

    # 3. Recompute Merkle root
    root, _ = build_merkle_tree(leaf_hashes)

    # 4. Verify signature
    sig_ok = verify_signature(inst["public_key_pem"], root, signature)
    if not sig_ok:
        return {
            "valid": False,
            "error": "Signature invalid — QR data has been tampered with or forged",
            "recomputed_root": root
        }

    # Signature is valid
    result = {
        "valid": True,
        "institution": inst_code,
        "recomputed_root": root,
        "authentic_fields": fields,
        "mismatches": []
    }

    # 5. Field-by-field comparison
    if printed_fields:
        for name, qr_value in fields.items():
            printed_value = printed_fields.get(name)
            if printed_value is None or str(printed_value).strip() == "":
                continue
            if canonicalize(name, qr_value) != canonicalize(name, printed_value):
                result["mismatches"].append({
                    "field": name,
                    "qr_value": str(qr_value),
                    "printed_value": str(printed_value)
                })

        if result["mismatches"]:
            result["status"] = "TAMPERING_DETECTED"
        else:
            result["status"] = "FULLY_VERIFIED"
    else:
        result["status"] = "SIGNATURE_VALID"

    return result