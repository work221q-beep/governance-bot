import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.sylas_chaos 

server_configs = db.server_configs
vuln_state = db.vulnerability_state 
payload_armory = db.payload_armory
guild_premium = db.guild_premium       # NEW: Tracks active subscriptions
guild_cooldowns = db.guild_cooldowns   # NEW: Tracks wargame usage

async def init_indexes():
    await vuln_state.create_index([("server_id", 1), ("vuln_name", 1)], unique=True)
    await server_configs.create_index("server_id", unique=True)
    await payload_armory.create_index("raid_type")
    await guild_premium.create_index("guild_id", unique=True)
    await guild_cooldowns.create_index([("guild_id", 1), ("raid_type", 1)], unique=True)

async def upsert_vulnerability(server_id: str, vuln_name: str, is_vulnerable: bool, details: str):
    status = "VULNERABLE" if is_vulnerable else "SECURE"
    existing = await vuln_state.find_one({"server_id": server_id, "vuln_name": vuln_name})
    prev_status = existing.get("status", "UNKNOWN") if existing else "UNKNOWN"

    await vuln_state.update_one(
        {"server_id": server_id, "vuln_name": vuln_name},
        {"$set": {
            "status": status, 
            "previous_status": prev_status,
            "details": details,
            "last_tested": datetime.utcnow()
        }},
        upsert=True
    )
