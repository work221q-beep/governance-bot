import os, asyncio, httpx, discord
from fastapi import FastAPI, Request, Form, BackgroundTasks, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bson import ObjectId

from bot import start_bot, bot, engine_state 
from ai import harvest_loop, harvest_payloads
from db import init_indexes, payload_armory

app = FastAPI()
templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "supersecret"))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
# Set this in your Render Environment Variables!
ADMIN_KEY = os.getenv("ADMIN_KEY", "masterkey123") 
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

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
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": serializer.loads(user_cookie)})

# ==========================================
# 🛑 MASTER ADMIN PANEL (PROTECTED)
# ==========================================
@app.get("/admin")
async def admin_panel(request: Request, key: str = None):
    # Secure Login via URL Parameter: ?key=your_password
    admin_auth = request.cookies.get("admin_auth")
    if key == ADMIN_KEY:
        response = RedirectResponse("/admin")
        response.set_cookie("admin_auth", "true", httponly=True)
        return response
    if admin_auth != "true":
        return HTMLResponse("Unauthorized - Invalid Master Key", status_code=403)

    # Ping AWS Ollama VM Live Status
    ollama_status = "OFFLINE"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            res = await c.get(f"{OLLAMA_URL}/api/tags")
            if res.status_code == 200: ollama_status = "ONLINE"
    except: pass

    payloads = await payload_armory.find().sort("created_at", -1).to_list(100)
    return templates.TemplateResponse("admin.html", {"request": request, "payloads": payloads, "bot_active": engine_state["active"], "ollama_status": ollama_status})

@app.post("/admin/toggle_bot")
async def toggle_bot(request: Request):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    engine_state["active"] = not engine_state["active"]
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/force_harvest")
async def admin_force_harvest(request: Request, bg_tasks: BackgroundTasks):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    bg_tasks.add_task(harvest_payloads, "phishing")
    bg_tasks.add_task(harvest_payloads, "ping")
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/delete_payload/{payload_id}")
async def admin_delete_payload(request: Request, payload_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    await payload_armory.delete_one({"_id": ObjectId(payload_id)})
    return RedirectResponse("/admin", status_code=303)

# ==========================================
# 🛡️ LIVE SYNC & PERMISSION MANAGER
# ==========================================
@app.get("/server/{guild_id}")
async def server_panel(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    guild = bot.get_guild(int(guild_id))
    roles = []
    if guild:
        for r in reversed(guild.roles): 
            current_perms = [perm[0] for perm in r.permissions if perm[1] is True]
            roles.append({"id": str(r.id), "name": r.name, "color": str(r.color) if r.color.value != 0 else None, "current": current_perms, "is_everyone": r.name == "@everyone", "is_bot": r.managed})
    return templates.TemplateResponse("server.html", {"request": request, "guild_id": guild_id, "roles": roles, "bot_in_server": bool(guild)})

@app.post("/server/{guild_id}/sync")
async def sync_server_permissions(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    form_data = await request.form()
    guild = bot.get_guild(int(guild_id))
    if guild:
        for r in guild.roles:
            selected_perms = form_data.getlist(f"perms_{r.id}")
            all_discord_perms = [p[0] for p in discord.Permissions()]
            new_kwargs = {perm: (perm in selected_perms) for perm in all_discord_perms}
            try: await r.edit(permissions=discord.Permissions(**new_kwargs), reason="Sylas Enterprise Sync")
            except discord.Forbidden: pass
    return RedirectResponse(f"/server/{guild_id}", status_code=303)

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str):
    """The Advanced Permission Manager for 100k servers."""
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    guild = bot.get_guild(int(guild_id))
    roles, users, bots, channels = [], [], [], []
    
    if guild:
        roles = [{"id": str(r.id), "name": r.name, "color": str(r.color) if r.color.value != 0 else None} for r in reversed(guild.roles)]
        
        for m in guild.members[:500]:
            member_data = {"id": str(m.id), "name": m.display_name, "avatar": m.display_avatar.url if m.display_avatar else None}
            if m.bot: bots.append(member_data)
            else: users.append(member_data)
            
        for c in guild.channels:
            channels.append({"id": str(c.id), "name": c.name, "type": str(c.type)})
            
    return templates.TemplateResponse("permissions.html", {"request": request, "guild_id": guild_id, "roles": roles, "users": users, "bots": bots, "channels": channels, "guild_name": guild.name if guild else "Unknown"})

@app.post("/server/{guild_id}/action/{action}/{target_id}")
async def mod_action(request: Request, guild_id: str, action: str, target_id: str):
    """Direct Mod Actions from the Web UI."""
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    guild = bot.get_guild(int(guild_id))
    if guild:
        target = guild.get_member(int(target_id))
        if target:
            try:
                if action == "kick": await target.kick(reason="Sylas Web Admin")
                elif action == "ban": await target.ban(reason="Sylas Web Admin")
                elif action == "timeout": await target.timeout(discord.utils.utcnow() + discord.utils.timedelta(minutes=10), reason="Sylas Web Admin")
            except discord.Forbidden: pass
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)
