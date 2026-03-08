import os
from cryptography.fernet import Fernet

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise RuntimeError("CRITICAL: ENCRYPTION_KEY environment variable must be set. Data loss will occur if the key is changed.")

fernet = Fernet(ENCRYPTION_KEY.encode())

def encrypt_data(data: str) -> str:
    if not data:
        return data
    return fernet.encrypt(data.encode()).decode()

def decrypt_data(data: str) -> str:
    if not data:
        return data
    try:
        return fernet.decrypt(data.encode()).decode()
    except Exception:
        # SECURITY FIX: Fail securely. Do not blindly return plaintext on decryption error.
        # This guarantees that unencrypted malicious injections are neutralized.
        return "[DECRYPTION_FAILED_OR_CORRUPT_DATA]"