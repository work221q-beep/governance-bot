import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.sylas_chaos 

server_configs = db.server_configs
vuln_state = db.vulnerability_state 

async def init_indexes():
    # Ensures we only have one entry per vulnerability type per server
    await vuln_state.create_index([("server_id", 1), ("vuln_name", 1)], unique=True)
    await server_configs.create_index("server_id", unique=True)

async def upsert_vulnerability(server_id: str, vuln_name: str, is_vulnerable: bool, details: str):
    """Updates the CURRENT state of a vulnerability. Overwrites old data."""
    status = "VULNERABLE" if is_vulnerable else "SECURE"
    await vuln_state.update_one(
        {"server_id": server_id, "vuln_name": vuln_name},
        {"$set": {
            "status": status, 
            "details": details,
            "last_tested": datetime.utcnow()
        }},
        upsert=True
    )
