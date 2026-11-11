from app.crypto.canonicalize import canonicalize
from app.crypto.merkle import sha256_hex, build_merkle_tree
from app.crypto.keys import generate_rsa_keypair, sign_data, verify_signature

print(canonicalize("CGPA", 8.723))
print(canonicalize("Name", "  Rahul Kumar  "))

leaves = [
    sha256_hex(canonicalize("Name", "Rahul")),
    sha256_hex(canonicalize("RegisterNo", "2018CS045")),
    sha256_hex(canonicalize("CGPA", 8.72)),
]
root, levels = build_merkle_tree(leaves)
print("Root:", root)

priv, pub = generate_rsa_keypair()
sig = sign_data(priv, root)
print("Signature valid?", verify_signature(pub, root, sig))