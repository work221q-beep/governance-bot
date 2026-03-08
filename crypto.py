import os
from cryptography.fernet import Fernet

# FIX 2: Strict environment variable requirement for Encryption Key
# Prevents volatile key generation that would cause permanent data loss on reboot
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
    except Exception as e:
        # If it's not a valid Fernet token, it might be legacy unencrypted data
        # Only return as-is if it doesn't look like a Fernet token (which starts with gAAAAA)
        if not str(data).startswith("gAAAAA"):
            return data
        print(f"CRITICAL: Failed to decrypt data: {e}")
        return "[DECRYPTION FAILED]"