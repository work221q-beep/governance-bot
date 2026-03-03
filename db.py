import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

MONGO_URI = os.getenv("MONGO_URI")

client = AsyncIOMotorClient(MONGO_URI)
db = client.statuscore

players = db.players
events = db.events
configs = db.configs

async def init_indexes():
    # Prevent cross-server stat bleed 
    await players.create_index([("server_id", 1), ("discord_id", 1)], unique=True)
    # Leaderboard indexes 
    await players.create_index([("server_id", 1), ("credibility", -1)])
    await players.create_index([("server_id", 1), ("fraudIndex", -1)])
    # Auto-delete events after 7 days 
    await events.create_index("createdAt", expireAfterSeconds=604800)

async def get_server_config(server_id: str):
    config = await configs.find_one({"server_id": server_id})
    if not config:
        default = {
            "server_id": server_id,
            "prefix": "!",
            "ai_enabled": True,
            "model": "phi3:mini",
            "temperature": 0.7
        }
        await configs.insert_one(default)
        return default
    return config

async def ensure_player(server_id: str, discord_id: str, username: str):
    player = await players.find_one({"server_id": server_id, "discord_id": discord_id})
    if player:
        await players.update_one({"_id": player["_id"]}, {"$set": {"username": username}})
        return player
    new_player = {
        "server_id": server_id, "discord_id": discord_id, "username": username,
        "fraudIndex": 0, "credibility": 50, "lastActive": datetime.utcnow(), "lastDecay": datetime.utcnow()
    }
    await players.insert_one(new_player)
    return new_player
