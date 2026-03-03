import os
import httpx
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

app = FastAPI()

# -------- MONGODB --------
MONGO_URI = os.getenv("MONGO_URI")
mongo_status = "not tested"

if MONGO_URI:
    try:
        client = AsyncIOMotorClient(MONGO_URI)
        db = client["governance"]
        mongo_status = "connected"
    except Exception as e:
        mongo_status = f"error: {str(e)}"
else:
    mongo_status = "MONGO_URI missing"

# -------- OLLAMA --------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def test_ollama():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                return "connected"
            return f"bad status {r.status_code}"
    except Exception as e:
        return f"error: {str(e)}"

# -------- ROUTES --------

@app.get("/")
async def root():
    return {"status": "Render running"}

@app.get("/test/mongo")
async def test_mongo():
    if mongo_status != "connected":
        return {"mongo": mongo_status}
    try:
        await db.test.insert_one({"hello": "world"})
        return {"mongo": "write success"}
    except Exception as e:
        return {"mongo": f"write failed: {str(e)}"}

@app.get("/test/ollama")
async def ollama_check():
    result = await test_ollama()
    return {"ollama": result}
