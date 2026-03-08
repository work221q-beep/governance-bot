import os
from cryptography.fernet import Fernet

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise RuntimeError("CRITICAL: ENCRYPTION_KEY environment variable must be set. Data loss will occur if the key is changed.")

fernet = Fernet(ENCRYPTION_KEY.encode())

MAX_PAYLOAD_SIZE = 50000  

def encrypt_data(data: str) -> str:
    if not data:
        return data
        
    data_str = str(data)
    if len(data_str) > MAX_PAYLOAD_SIZE:
        return "[PAYLOAD_TOO_LARGE]"
        
    return fernet.encrypt(data_str.encode()).decode()

def decrypt_data(data: str) -> str:
    if not data:
        return data
        
    data_str = str(data)
    if len(data_str) > MAX_PAYLOAD_SIZE:
        return "[DECRYPTION_FAILED_OR_CORRUPT_DATA]"
        
    try:
        return fernet.decrypt(data_str.encode()).decode()
    except Exception:
        return "[DECRYPTION_FAILED_OR_CORRUPT_DATA]"