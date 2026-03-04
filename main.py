import os, asyncio, httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from datetime import datetime
from bot import start_bot, bot # Importing bot to access Discord cache
from db import init_indexes, server_configs, vuln_state, role_baselines

app = FastAPI()
templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "supersecret"))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())

@app.get("/")
async def home(request: Request):
    user_cookie = request.cookies.get("session")
    user = None
    if user_cookie:
        try: user = serializer.loads(user_cookie)
        except: user = None
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/invite")
async def invite_bot():
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions=8&scope=bot"
    return RedirectResponse(url)

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
        access_token = token_res.json().get("access_token")
        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        guild_res = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
        user, guilds = user_res.json(), guild_res.json()

    # STRICT ENFORCEMENT: Only allow guilds where user is Administrator (0x8) or Owner
    manageable_guilds = [g for g in guilds if g["owner"] or (int(g["permissions"]) & 0x8) == 0x8]
    session_data = {"id": user["id"], "username": user["username"], "guilds": manageable_guilds}
    response = RedirectResponse(url="/dashboard")
    response.set_cookie("session", serializer.dumps(session_data), httponly=True)
    return response

@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    try: user = serializer.loads(user_cookie)
    except: return RedirectResponse("/")
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@app.get("/server/{guild_id}")
async def server_panel(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    try: user = serializer.loads(user_cookie)
    except: return RedirectResponse("/")
    allowed = any(g["id"] == guild_id for g in user["guilds"])
    if not allowed: return HTMLResponse("Access Denied: Administrator Required", status_code=403)
    
    config = await server_configs.find_one({"server_id": guild_id}) or {"model": "llama3"}
    models = ["phi3:mini", "llama3", "mistral"] 

    # Fetch live roles from the Discord Bot Cache
    guild = bot.get_guild(int(guild_id))
    roles = []
    if guild:
        baselines = await role_baselines.find({"server_id": guild_id}).to_list(100)
        baseline_map = {b["role_id"]: b.get("allowed_perms", []) for b in baselines}
        
        for r in guild.roles:
            if r.name != "@everyone" and not r.managed:
                roles.append({
                    "id": str(r.id), 
                    "name": r.name, 
                    "allowed": baseline_map.get(str(r.id), [])
                })
    
    return templates.TemplateResponse("server.html", {
        "request": request, "guild_id": guild_id, "config": config, "models": models, "roles": roles, "bot_in_server": bool(guild)
    })

@app.post("/server/{guild_id}/update")
async def update_server(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    
    form_data = await request.form()
    prefix = form_data.get("prefix", "!")
    model = form_data.get("model", "llama3")
    
    await server_configs.update_one({"server_id": guild_id}, {"$set": {"prefix": prefix, "model": model}}, upsert=True)

    # Process Role Permissions
    guild = bot.get_guild(int(guild_id))
    if guild:
        for r in guild.roles:
            if r.name != "@everyone" and not r.managed:
                # Get all checked checkboxes for this specific role
                allowed_perms = form_data.getlist(f"perms_{r.id}")
                await role_baselines.update_one(
                    {"server_id": guild_id, "role_id": str(r.id)},
                    {"$set": {"allowed_perms": allowed_perms}},
                    upsert=True
                )

    return RedirectResponse(f"/server/{guild_id}", status_code=303)

@app.get("/server/{guild_id}/audit")
async def server_audit(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    vulns = await vuln_state.find({"server_id": guild_id}).to_list(100)
    total_tests = len(vulns)
    secure_tests = sum(1 for v in vulns if v["status"] == "SECURE")
    security_score = int((secure_tests / total_tests) * 100) if total_tests > 0 else 100
    return templates.TemplateResponse("leaderboard.html", {
        "request": request, "guild_id": guild_id, "vulns": vulns, "score": security_score
    })
