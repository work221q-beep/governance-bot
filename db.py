import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.get_default_database()

admins = db["admins"]
servers = db["servers"]
configs = db["configs"]
logs = db["logs"]


async def get_server_config(server_id: str):
    config = await configs.find_one({"server_id": server_id})

    if not config:
        default = {
            "server_id": server_id,
            "prefix": "!",
            "ai_enabled": True,
            "respond_every_message": False,
            "allowed_channels": [],
            "model": "phi3:mini",   # ✅ CHANGED HERE
            "temperature": 0.7,
            "rate_limit_per_min": 10
        }
        await configs.insert_one(default)
        return default

    return config
