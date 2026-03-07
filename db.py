import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.sylas_chaos 

payload_armory = db.payload_armory
guild_premium = db.guild_premium       # NEW: Tracks active subscriptions
guild_cooldowns = db.guild_cooldowns   # NEW: Tracks wargame usage
license_keys = db.license_keys         # NEW: Tracks premium license keys
payments = db.payments                 # NEW: Tracks Chain2Pay payments
premium_gifts = db.premium_gifts       # NEW: Tracks gifted premium

async def init_indexes():
    await payload_armory.create_index("raid_type")
    await guild_premium.create_index("guild_id", unique=True)
    await guild_cooldowns.create_index([("guild_id", 1), ("raid_type", 1)], unique=True)
    await license_keys.create_index("key", unique=True)
