import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI environment variable is required")

client = AsyncIOMotorClient(
    MONGO_URI,
    maxPoolSize=50,
    minPoolSize=10,
    serverSelectionTimeoutMS=5000
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
    
    # Session TTL indexes
    await sessions.create_index("session_id", unique=True)
    await sessions.create_index("expires_at", expireAfterSeconds=0)
    await admin_sessions.create_index("expires_at", expireAfterSeconds=0) # FIX 6: Admin session MongoDB TTL
    
    # FIX 8: Compound Indexes
    await audit_logs.create_index([("guild_id", 1), ("timestamp", -1)])
    await gift_logs.create_index([("guild_id", 1), ("timestamp", -1)])