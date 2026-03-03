import asyncio
from fastapi import FastAPI
from bot import start_bot
from db import init_indexes, players
from datetime import datetime, timedelta

app = FastAPI()

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

        await asyncio.sleep(3600)  # check hourly


@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(decay_cycle())


@app.get("/health")
async def health():
    return {"status": "ok"}
