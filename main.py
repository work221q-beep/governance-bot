import os, asyncio, httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from datetime import datetime
from bot import start_bot
from db import init_indexes, players, configs, get_server_config

app = FastAPI()
templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "supersecret"))

# --- Background Decay Logic  ---
async def decay_cycle():
    while True:
        try:
            now = datetime.utcnow()
            async for player in players.find({}):
                last_decay = player.get("lastDecay", now)
                if (now - last_decay).total_seconds() / 3600 >= 24:
                    new_fraud = max(0, player.get("fraudIndex", 0) - 5)
                    await players.update_one({"_id": player["_id"]}, {"$set": {"fraudIndex": new_fraud, "lastDecay": now}})
        except Exception as e: print(f"Decay error: {e}")
        await asyncio.sleep(3600)

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(decay_cycle())

# --- Web Routes  ---
@app.get("/")
async def home(request: Request):
    user = serializer.loads(request.cookies.get("session")) if request.cookies.get("session") else None
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/dashboard")
async def dashboard(request: Request):
    user = serializer.loads(request.cookies.get("session"))
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@app.get("/server/{guild_id}/leaderboard")
async def server_leaderboard(request: Request, guild_id: str):
    top_cred = await players.find({"server_id": guild_id}).sort("credibility", -1).limit(10).to_list(10)
    top_fraud = await players.find({"server_id": guild_id}).sort("fraudIndex", -1).limit(10).to_list(10)
    return templates.TemplateResponse("leaderboard.html", {"request": request, "guild_id": guild_id, "top_cred": top_cred, "top_fraud": top_fraud})

@app.post("/server/{guild_id}/update")
async def update_server(guild_id: str, prefix: str = Form(...), model: str = Form(...), ai_enabled: str = Form(None)):
    await configs.update_one({"server_id": guild_id}, {"$set": {"prefix": prefix, "model": model, "ai_enabled": bool(ai_enabled)}})
    return RedirectResponse(f"/server/{guild_id}", status_code=303)
