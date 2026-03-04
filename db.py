import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.sylas_chaos 

audit_logs = db.audit_logs
mod_scores = db.mod_scores
server_configs = db.server_configs # <-- This was missing!

async def init_indexes():
    # Auto-delete test logs after 7 days (saves your 512MB Mongo limit)
    await audit_logs.create_index("timestamp", expireAfterSeconds=604800)
    await mod_scores.create_index([("server_id", 1), ("mod_id", 1)], unique=True)

async def log_probe(server_id: str, probe_type: str, status: str, details: str):
    await audit_logs.insert_one({
        "server_id": server_id,
        "probe_type": probe_type,
        "status": status, 
        "details": details,
        "timestamp": datetime.utcnow()
    })
