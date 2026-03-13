import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI")

# SECURITY FIX: Restored Motor Async for FastAPI/Discord compatibility,
# but enforced strict pool limits to prevent connection exhaustion.
client = AsyncIOMotorClient(
    MONGO_URI,
    maxPoolSize=50,
    minPoolSize=5,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=20000
)

db = client.sylas_chaos 

payload_armory = db.payload_armory
guild_premium = db.guild_premium
guild_cooldowns = db.guild_cooldowns
license_keys = db.license_keys
payments = db.payments
gift_logs = db.gift_logs
sessions = db.sessions
audit_logs = db.audit_logs
admin_sessions = db.admin_sessions

async def init_indexes():
    await payload_armory.create_index("raid_type")
    await guild_premium.create_index("guild_id", unique=True)
    await guild_cooldowns.create_index([("guild_id", 1), ("raid_type", 1)], unique=True)
    await license_keys.create_index("key", unique=True)
    await payments.create_index("internal_order_id", unique=True)
    await payments.create_index("paymento_token", sparse=True)
    await sessions.create_index("session_id", unique=True)
    await sessions.create_index("expires_at", expireAfterSeconds=0)
    await admin_sessions.create_index("token", unique=True)
    await admin_sessions.create_index("expires_at", expireAfterSeconds=0)