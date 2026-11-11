from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import base64
import os
from flask import current_app

def generate_rsa_keypair(bits=2048):
    key = RSA.generate(bits)
    private_pem = key.export_key().decode('utf-8')
    public_pem = key.publickey().export_key().decode('utf-8')
    return private_pem, public_pem

def sign_data(private_pem: str, data: str) -> str:
    key = RSA.import_key(private_pem)
    h = SHA256.new(data.encode('utf-8'))
    signature = pkcs1_15.new(key).sign(h)
    return base64.b64encode(signature).decode('utf-8')

def verify_signature(public_pem: str, data: str, signature_b64: str) -> bool:
    key = RSA.import_key(public_pem)
    h = SHA256.new(data.encode('utf-8'))
    try:
        pkcs1_15.new(key).verify(h, base64.b64decode(signature_b64))
        return True
    except (ValueError, TypeError):
        return False

def save_private_key(private_pem: str):
    path = os.path.join(current_app.instance_path, "private_key.pem")
    with open(path, "w") as f:
        f.write(private_pem)

def load_private_key() -> str:
    path = os.path.join(current_app.instance_path, "private_key.pem")
    if not os.path.exists(path):
        raise FileNotFoundError("Private key not found. Generate institution first.")
    with open(path, "r") as f:
        return f.read()