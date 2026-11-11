import json
import base64
import zlib
import qrcode
from PIL import Image

# ==========================================================
# YOUR ORIGINAL COMPRESSED PAYLOAD
# ==========================================================
original_compressed = """CT1:eNp9kUtvqlAUhf+KYWqtwEFFkw54HBCRA/IQZNKgPERAkIeWNv3v93gH3ps0drb3+tZa2cn+ItJz0xILwtIRR7wQbV9FeGuiImraqMbKY1wwL0ScRnnYEIsvAgXF3bjWVWgtXc7k0MDEbjNK0rsblRhOqRlLAxpQJE2yFKZGXSZ1UPxN8q9wMBoIumY4NjQHlqBAJMABh8QBRLKCIDQVJOOQ0jRdJAbtPUST9HREzkfkFAPrcRnB/LfKBocV9nUyw6JQdnUTvTs0ECyGpN7lOgjvRdwPRv/CwC+MeTD9B5s8YVBgyNmTThk+v/PO6CedGmfjIPsvOSS+8ffSBM8H60T3jksvY9DarZ9R8nIanoMKSn1S3XbOsjIvGsmwqnKhmny1E8dSHJTS4VPN5SZrA2Mt276xvSx9VosOmqf7djyJhj3lD22ZAUlRU+vOUtsjz5TZampTE31nCZeGOp7i63lzqULS9GQb7SdecAs3JJmZ+Xyd4Z+zRgukmIfu5Zzz0m2VIgkUaLsPG0lQPN+DXSWKIoRS64XHMC2PaarGQ+d8M6+qJTGJf5D2Lmfn2W6D0MdQrTZZYSHQwrGjx9dqljrSQZ/RVV9oc6Yam4oCnFPRg25cdyD3xKxgs+4qQ/d8Tcs1METRoKbmJqmrrcV2nSNphz2Yicx4sutsF+jHT/qDR+Mb0/Dc2xvx/Qca8v8Q"""

# ==========================================================
# DECOMPRESS
# ==========================================================
compressed_data = base64.b64decode(original_compressed[4:])  # remove "CT1:"
print(compressed_data)
json_str = zlib.decompress(compressed_data).decode('utf-8')
data = json.loads(json_str)

print("========== ORIGINAL FIELDS ==========")
for k, v in data["fields"].items():
    print(f"{k:30} : {v}")

# ==========================================================
# MAKE CHANGES HERE (Tamper)
# ==========================================================
data["fields"]["Name"] = "LOKESHWARAN R MODIFIED"
data["fields"]["SemesterGPA"] = "9.99"
data["fields"]["Course_U23CS401_Grade"] = "O"      # was A
data["fields"]["Course_U23MAT4018_Grade"] = "B+"   # was A+

# ==========================================================
# RE-COMPRESS
# ==========================================================
new_json = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
new_compressed = zlib.compress(new_json.encode('utf-8'), level=9)
tampered_payload = "CT1:" + base64.b64encode(new_compressed).decode('ascii')

print("\n========== TAMPERED PAYLOAD ==========")
print(tampered_payload)

# ==========================================================
# GENERATE QR
# ==========================================================
qr = qrcode.QRCode(
    version=None,
    error_correction=qrcode.constants.ERROR_CORRECT_M,
    box_size=10,
    border=4
)
qr.add_data(tampered_payload)
qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
img.save("tampered_LOKESHWARAN_QR.png")

print("\nTampered QR saved as → tampered_LOKESHWARAN_QR.png")