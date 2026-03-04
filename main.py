import os, asyncio, httpx, discord, datetime, json
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bson import ObjectId
import urllib.parse

from bot import start_bot, bot, engine_state 
from ai import harvest_loop, harvest_payloads
from db import init_indexes, payload_armory, vuln_state, server_configs

app = FastAPI()
templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "supersecret"))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ADMIN_KEY = os.getenv("ADMIN_KEY", "masterkey123") 
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# YOUR DISCORD ID GOES HERE
MASTER_DISCORD_ID = os.getenv("MASTER_DISCORD_ID", "YOUR_DISCORD_ID_HERE") 

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(harvest_loop())

@app.get("/")
async def home(request: Request):
    user_cookie = request.cookies.get("session")
    user = serializer.loads(user_cookie) if user_cookie else None
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login")
async def login():
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={DISCORD_REDIRECT_URI}&scope=identify%20guilds"
    return RedirectResponse(url)

@app.get("/invite")
async def invite_bot():
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions=8&scope=bot"
    return RedirectResponse(url)

@app.get("/auth/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data={
            "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI
        })
        access_token = token_res.json().get("access_token")
        user = (await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})).json()
        guilds = (await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})).json()

    manageable_guilds = [g for g in guilds if g["owner"] or (int(g["permissions"]) & 0x8) == 0x8]
    response = RedirectResponse(url="/dashboard")
    response.set_cookie("session", serializer.dumps({"id": user["id"], "username": user["username"], "guilds": manageable_guilds}), httponly=True)
    return response

@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    session_user = serializer.loads(user_cookie)
    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": session_user, "is_master": is_master})

# ==========================================
# 🛑 MASTER ADMIN PANEL (PROTECTED)
# ==========================================
@app.get("/admin")
async def admin_panel(request: Request, key: str = None):
    admin_auth = request.cookies.get("admin_auth")
    if key == ADMIN_KEY:
        response = RedirectResponse("/admin")
        response.set_cookie("admin_auth", "true", httponly=True)
        return response
    if admin_auth != "true": return HTMLResponse("Unauthorized - Invalid Key", status_code=403)

    ollama_status = "OFFLINE"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            res = await c.get(f"{OLLAMA_URL}/api/tags")
            if res.status_code == 200: ollama_status = "ONLINE"
    except: pass

    payloads = await payload_armory.find().sort("created_at", -1).to_list(100)
    
    # Raw DB Fetch for the Explorer
    vulns = await vuln_state.find().sort("last_tested", -1).to_list(50)
    configs = await server_configs.find().to_list(50)
    
    # 🐛 THE FIX: Convert ObjectIds AND DateTimes to strings so Jinja can render the JSON safely
    for v in vulns: 
        v["_id"] = str(v["_id"])
        if "last_tested" in v and isinstance(v["last_tested"], datetime.datetime):
            v["last_tested"] = v["last_tested"].isoformat()
            
    for c in configs: 
        c["_id"] = str(c["_id"])
    
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], 
        "ollama_status": ollama_status, "vulns": vulns, "configs": configs
    })

@app.post("/admin/toggle_bot")
async def toggle_bot(request: Request):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    engine_state["active"] = not engine_state["active"]
    return RedirectResponse("/admin?tab=control", status_code=303)

@app.post("/admin/force_harvest")
async def admin_force_harvest(request: Request, bg_tasks: BackgroundTasks):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    bg_tasks.add_task(harvest_payloads, "phishing")
    bg_tasks.add_task(harvest_payloads, "ping")
    return RedirectResponse("/admin?tab=armory", status_code=303)

@app.post("/admin/delete_payload/{payload_id}")
async def admin_delete_payload(request: Request, payload_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    await payload_armory.delete_one({"_id": ObjectId(payload_id)})
    return RedirectResponse("/admin?tab=armory", status_code=303)

@app.post("/admin/purge_armory")
async def admin_purge_armory(request: Request):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    await payload_armory.delete_many({})
    return RedirectResponse("/admin
