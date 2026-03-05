import os, asyncio, httpx, discord, datetime, json
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bson import ObjectId
from bot import start_bot, bot, engine_state
from ai import harvest_loop, harvest_payloads
from db import init_indexes, payload_armory, vuln_state, server_configs

app = FastAPI()
templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "supersecret"))

# URL Configuration - Using your dedicated BASE_URL variable
BASE_URL = os.getenv("BASE_URL", "https://governance-bot.onrender.com").rstrip("/") 
DISCORD_REDIRECT_URI = f"{BASE_URL}/callback"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
ADMIN_KEY = os.getenv("ADMIN_KEY", "masterkey123")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") 
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
    return templates.TemplateResponse("index.html", {"request": request, "user": user, "base_url": BASE_URL})

@app.get("/login")
async def login():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={httpx.utils.quote(DISCORD_REDIRECT_URI)}"
        f"&response_type=code&scope=identify%20guilds"
    )
    return RedirectResponse(auth_url)

@app.get("/callback")
async def callback(code: str):
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
        token_json = token_res.json()
        access_token = token_json.get("access_token")

        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        user_data = user_res.json()

        guilds_res = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
        guilds_data = guilds_res.json()
        
    admin_guilds = [g for g in guilds_data if (int(g["permissions"]) & 0x8) == 0x8]
    session_data = {
        "id": user_data["id"],
        "username": user_data["username"],
        "avatar": f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get("avatar") else None,
        "guilds": admin_guilds
    }

    response = RedirectResponse(f"{BASE_URL}/dashboard")
    response.set_cookie("session", serializer.dumps(session_data), max_age=86400, httponly=True)
    return response

@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse(f"{BASE_URL}/")
    user = serializer.loads(user_cookie)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "base_url": BASE_URL})

@app.get("/server/{server_id}")
async def server_panel(request: Request, server_id: str):
    if not server_id or server_id == "None": return RedirectResponse(f"{BASE_URL}/dashboard")
    
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse(f"{BASE_URL}/")
    user = serializer.loads(user_cookie)
    
    guild = bot.get_guild(int(server_id))
    bot_present = guild is not None
    channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels] if bot_present else []
    roles = [{"id": str(r.id), "name": r.name, "color": str(r.color)} for r in guild.roles if r.name != "@everyone"] if bot_present else []
    
    config = await server_configs.find_one({"server_id": server_id}) or {}
    
    return templates.TemplateResponse("server.html", {
        "request": request, "user": user, "server_id": server_id, "server_name": guild.name if bot_present else "Unknown Server",
        "bot_present": bot_present, "channels": channels, "roles": roles, "config": config, "base_url": BASE_URL
    })

@app.get("/server/{server_id}/permissions")
async def permissions_panel(request: Request, server_id: str):
    if not server_id or server_id == "None": return RedirectResponse(f"{BASE_URL}/dashboard")
    
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse(f"{BASE_URL}/")
    user = serializer.loads(user_cookie)
    
    guild = bot.get_guild(int(server_id))
    bot_present = guild is not None
    roles = [{"id": str(r.id), "name": r.name, "color": str(r.color)} for r in guild.roles if r.name != "@everyone"] if bot_present else []
    channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels] if bot_present else []
    
    vulns = await vuln_state.find({"server_id": server_id}).to_list(100)
    
    return templates.TemplateResponse("permissions.html", {
        "request": request, "user": user, "server_id": server_id, "server_name": guild.name if bot_present else "Unknown Server",
        "bot_present": bot_present, "roles": roles, "channels": channels, "vulns": vulns, "base_url": BASE_URL
    })

@app.get("/admin")
async def master_admin(request: Request, key: str = None, tab: str = "control"):
    auth_cookie = request.cookies.get("admin_auth")
    if auth_cookie != "true" and key != ADMIN_KEY:
        return RedirectResponse(f"{BASE_URL}/")
        
    response = HTMLResponse()
    if key == ADMIN_KEY:
        response.set_cookie("admin_auth", "true", max_age=86400, httponly=True)
        
    payloads = await payload_armory.find().sort("created_at", -1).to_list(100)
    
    db_structure = {}
    colls = {"server_configs": server_configs, "vulnerability_state": vuln_state, "payload_armory": payload_armory}
    for label, collection in colls.items():
        docs = await collection.find().to_list(100)
        for d in docs: 
            d["_id"] = str(d["_id"])
            if "created_at" in d and isinstance(d["created_at"], datetime.datetime):
                d["created_at"] = d["created_at"].isoformat()
        db_structure[label] = docs
        
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], 
        "ollama_status": "NVIDIA-CLOUD", 
        "db_structure": db_structure,
        "base_url": BASE_URL
    })

@app.post("/admin/toggle_bot")
async def toggle_bot(request: Request):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse(f"{BASE_URL}/")
    engine_state["active"] = not engine_state["active"]
    return RedirectResponse(f"{BASE_URL}/admin?tab=control", status_code=303)

@app.post("/admin/force_harvest")
async def admin_force_harvest(request: Request, bg_tasks: BackgroundTasks):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse(f"{BASE_URL}/")
    bg_tasks.add_task(harvest_payloads, "phishing")
    bg_tasks.add_task(harvest_payloads, "ping")
    bg_tasks.add_task(harvest_payloads, "innocent")
    return RedirectResponse(f"{BASE_URL}/admin?tab=armory", status_code=303)

@app.get("/logout")
async def logout():
    response = RedirectResponse(f"{BASE_URL}/")
    response.delete_cookie("session")
    response.delete_cookie("admin_auth")
    return response

@app.post("/server/{server_id}/save")
async def save_config(request: Request, server_id: str):
    form = await request.form()
    await server_configs.update_one(
        {"server_id": server_id},
        {"$set": {"log_channel": form.get("log_channel"), "alert_channel": form.get("alert_channel")}},
        upsert=True
    )
    return RedirectResponse(f"{BASE_URL}/server/{server_id}", status_code=303)

@app.post("/admin/delete_payload/{payload_id}")
async def admin_delete_payload(request: Request, payload_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse(f"{BASE_URL}/")
    await payload_armory.delete_one({"_id": ObjectId(payload_id)})
    return RedirectResponse(f"{BASE_URL}/admin?tab=armory", status_code=303)

@app.post("/admin/db/delete_doc/{collection}/{doc_id}")
async def delete_doc(request: Request, collection: str, doc_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse(f"{BASE_URL}/")
    target = server_configs if collection == "server_configs" else vuln_state if collection == "vulnerability_state" else payload_armory
    await target.delete_one({"_id": ObjectId(doc_id)})
    return RedirectResponse(f"{BASE_URL}/admin?tab=database", status_code=303)

@app.post("/admin/db/edit_doc/{collection}/{doc_id}")
async def edit_doc(request: Request, collection: str, doc_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse(f"{BASE_URL}/")
    form = await request.form()
    raw_json = form.get("raw_json")
    try:
        updated_data = json.loads(raw_json)
        if "_id" in updated_data: del updated_data["_id"]
        target = server_configs if collection == "server_configs" else vuln_state if collection == "vulnerability_state" else payload_armory
        await target.update_one({"_id": ObjectId(doc_id)}, {"$set": updated_data})
    except Exception as e:
        print("JSON Error:", e)
    return RedirectResponse(f"{BASE_URL}/admin?tab=database", status_code=303)
