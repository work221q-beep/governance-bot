import os
from pymongo import AsyncMongoClient

# Using Native PyMongo Async to avoid the Motor EOL Vulnerability
client = AsyncMongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB_NAME", "sylas_db")]

payload_armory = db["payload_armory"]
guild_premium = db["guild_premium"]
guild_cooldowns = db["guild_cooldowns"]
license_keys = db["license_keys"]
payments = db["payments"]
gift_logs = db["gift_logs"]
sessions = db["sessions"]
audit_logs = db["audit_logs"]
admin_sessions = db["admin_sessions"]

async def init_indexes():
    await payload_armory.create_index("raid_type")
    await sessions.create_index("session_id", unique=True)
    await sessions.create_index("expires_at", expireAfterSeconds=0)
    await admin_sessions.create_index("token", unique=True)
    await admin_sessions.create_index("expires_at", expireAfterSeconds=0)
    await license_keys.create_index("key", unique=True)