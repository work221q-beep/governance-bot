import os
import asyncio
import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from datetime import datetime

# Import your custom modules
from bot import start_bot
from db import init_indexes, players, configs, get_server_config
from decay import run_decay_cycle # Ensure you have this file or the logic below

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Environment Variables
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")

serializer = URLSafeSerializer(SECRET_KEY)

# --- DECAY SETTINGS ---
FRAUD_DECAY = 5
DECAY_INTERVAL_HOURS = 24

async def decay_scheduler():
    while True:
        try:
            print("Running daily decay cycle...")
            # Inline decay logic if you don't have decay.py
            now = datetime.utcnow()
            cursor = players.find({})
            async for player in cursor:
                last_decay = player.get("lastDecay", now)
                hours_since = (now - last_decay).total_seconds() / 3600
                if hours_since >= DECAY_INTERVAL_HOURS:
                    current_fraud = player.get("fraudIndex", 0)
                    await players.update_one(
                        {"_id": player["_id"]},
                        {"$set": {"fraudIndex": max(0, current_fraud - FRAUD_DECAY), "lastDecay": now}}
                    )
            print("Decay complete.")
        except Exception as e:
            print("Decay error:", e)
        await asyncio.sleep(3600) # Check every hour

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(decay_scheduler())

# --- ORIGINAL WEB INTERFACE ROUTES ---

@app.get("/")
async def home(request: Request):
    user_cookie = request.cookies.get("session")
    user = serializer.loads(user_cookie) if user_cookie else None
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login")
async def login():
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={DISCORD_REDIRECT_URI}&scope=identify%20guilds"
    return RedirectResponse(url)

@app.get("/auth/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data={
            "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI
        })
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        guild_res = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
        user, guilds = user_res.json(), guild_res.json()

    manageable_guilds = [g for g in guilds if g["owner"] or (int(g["permissions"]) & 0x20)]
    session_data = {"id": user["id"], "username": user["username"], "guilds": manageable_guilds}
    
    response = RedirectResponse(url="/dashboard")
    response.set_cookie("session", serializer.dumps(session_data), httponly=True)
    return response

@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    user = serializer.loads(user_cookie)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

# --- STATUSCORE ARENA ROUTES ---

@app.get("/server/{guild_id}/leaderboard")
async def server_leaderboard(request: Request, guild_id: str):
    top_cred = await players.find({"server_id": guild_id}).sort("credibility", -1).limit(10).to_list(10)
    top_fraud = await players.find({"server_id": guild_id}).sort("fraudIndex", -1).limit(10).to_list(10)
    return templates.TemplateResponse("leaderboard.html", {"request": request, "guild_id": guild_id, "top_cred": top_cred, "top_fraud": top_fraud})

@app.get("/health")
async def health():
    return {"status": "ok"}
