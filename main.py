import asyncio
from fastapi import FastAPI
from bot import start_bot
from db import init_indexes, players
from datetime import datetime

app = FastAPI()

FRAUD_DECAY = 5


async def decay_cycle():
    while True:
        try:
            now = datetime.utcnow()
            cursor = players.find({})

            async for player in cursor:
                if player["fraudIndex"] > 0:
                    await players.update_one(
                        {"_id": player["_id"]},
                        {"$inc": {"fraudIndex": -FRAUD_DECAY}}
                    )

            print("Decay cycle complete.")

        except Exception as e:
            print("Decay error:", e)

        await asyncio.sleep(86400)


@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(decay_cycle())


@app.get("/health")
async def health():
    return {"status": "ok"}
