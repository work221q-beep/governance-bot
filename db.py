import os
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.get_default_database()

configs = db["configs"]
logs = db["logs"]

players = db["players"]
events = db["events"]


async def init_indexes():
    await players.create_index("discord_id", unique=True)
    await players.create_index("fraudIndex")
    await events.create_index("createdAt", expireAfterSeconds=604800)  # 7 days TTL


async def ensure_player(discord_id: str, username: str):
    player = await players.find_one({"discord_id": discord_id})

    if player:
        return player

    new_player = {
        "discord_id": discord_id,
        "username": username,
        "fraudIndex": 0,
        "credibility": 50,
        "clutchFactor": 0,
        "lastActive": datetime.utcnow(),
        "lastDecay": datetime.utcnow()
    }

    await players.insert_one(new_player)
    return new_player


async def get_server_config(server_id: str):
    config = await configs.find_one({"server_id": server_id})

    if not config:
        default = {
            "server_id": server_id,
            "ai_enabled": True,
            "model": "phi3:mini",
            "temperature": 0.7
        }
        await configs.insert_one(default)
        return default

    return config
