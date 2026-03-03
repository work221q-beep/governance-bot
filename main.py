import asyncio
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from db import init_indexes, players
from bot import start_bot
from datetime import datetime

app = FastAPI()
templates = Jinja2Templates(directory="templates")

FRAUD_DECAY = 5
DECAY_INTERVAL_HOURS = 24


async def decay_cycle():
    while True:
        try:
            now = datetime.utcnow()
            cursor = players.find({})

            async for player in cursor:
                last_decay = player.get("lastDecay", now)
                hours_since = (now - last_decay).total_seconds() / 3600

                if hours_since < DECAY_INTERVAL_HOURS:
                    continue

                current_fraud = player.get("fraudIndex", 0)
                new_fraud = max(0, current_fraud - FRAUD_DECAY)

                await players.update_one(
                    {"_id": player["_id"]},
                    {
                        "$set": {
                            "fraudIndex": new_fraud,
                            "lastDecay": now
                        }
                    }
                )

            print("Decay cycle complete.")

        except Exception as e:
            print("Decay error:", e)

        await asyncio.sleep(3600)


@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(decay_cycle())


@app.get("/server/{guild_id}/leaderboard")
async def server_leaderboard(request: Request, guild_id: str):
    top_cred = await players.find(
        {"server_id": guild_id}
    ).sort("credibility", -1).limit(10).to_list(10)

    top_fraud = await players.find(
        {"server_id": guild_id}
    ).sort("fraudIndex", -1).limit(10).to_list(10)

    return templates.TemplateResponse(
        "leaderboard.html",
        {
            "request": request,
            "guild_id": guild_id,
            "top_cred": top_cred,
            "top_fraud": top_fraud
        }
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
