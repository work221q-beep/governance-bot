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

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ADMIN_KEY = os.getenv("ADMIN_KEY", "masterkey123")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
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

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("session")
    response.delete_cookie("admin_auth")
    return response

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
    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png" if user.get("avatar") else None
    
    response = RedirectResponse(url="/dashboard")
    response.set_cookie("session", serializer.dumps({
        "id": user["id"], "username": user["username"], "avatar": avatar_url, "guilds": manageable_guilds
    }), httponly=True)
    return response

@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    session_user = serializer.loads(user_cookie)
    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": session_user, "is_master": is_master})

@app.get("/server/{guild_id}")
async def redirect_to_permissions(guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions")

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    
    roles, users, bots, channels = [], [], [], []
    
    if guild:
        for r in reversed(guild.roles):
            roles.append({
                "id": str(r.id), "name": r.name, 
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "is_everyone": r.name == "@everyone", "is_bot": r.managed
            })
            
        for m in guild.members:
            # 🛑 CRITICAL FIX: Safe fallback for users with no profile pictures
            avatar = str(m.display_avatar.url) if m.display_avatar else None
            member_data = {
                "id": str(m.id), "name": m.name, "display_name": m.display_name,
                "avatar": avatar, "top_role": m.top_role.name if m.top_role else "None"
            }
            if m.bot: bots.append(member_data)
            else: users.append(member_data)
                
        for c in guild.channels:
            channels.append({"id": str(c.id), "name": c.name, "type": str(c.type)})

    return templates.TemplateResponse("permissions.html", {
        "request": request, "guild_id": guild_id, "roles": roles, "users": users, 
        "bots": bots, "channels": channels, "guild_name": guild.name if guild else "Unknown Server",
        "user": session_user
    })

@app.post("/server/{guild_id}/action/{action}/{target_id}")
async def mod_action(request: Request, guild_id: str, action: str, target_id: str):
    """Executes moderation with strictly enforced Role Hierarchy checks."""
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    target = guild.get_member(int(target_id))
    web_member = guild.get_member(int(session_user.get("id")))
    
    if target and web_member:
        # Prevent lower roles from moderating higher roles
        if guild.owner_id != web_member.id and web_member.top_role <= target.top_role:
            return HTMLResponse(f"Access Denied: You cannot {action} a user with an equal or higher role.", status_code=403)
            
        try:
            if action == "kick": await target.kick(reason=f"Sylas Web Admin ({web_member.name})")
            elif action == "ban": await target.ban(reason=f"Sylas Web Admin ({web_member.name})")
            elif action == "timeout": await target.timeout(discord.utils.utcnow() + discord.utils.timedelta(minutes=10), reason=f"Sylas Web Admin ({web_member.name})")
        except discord.Forbidden:
            pass # Bot doesn't have permissions
            
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)

@app.post("/server/{guild_id}/channel/{channel_id}/override")
async def channel_override(request: Request, guild_id: str, channel_id: str):
    """Updates explicit Channel Overrides (Allow/Deny/Inherit) for a specific role."""
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    
    form_data = await request.form()
    role_id = form_data.get("role_id")
    
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    channel = guild.get_channel(int(channel_id))
    role = guild.get_role(int(role_id)) if role_id else guild.default_role
    
    if channel and role:
        overwrite = channel.overwrites_for(role)
        # Apply the explicit matrix selections
        for perm in ["view_channel", "send_messages", "embed_links", "attach_files", "manage_messages"]:
            val = form_data.get(perm)
            if val == "allow": setattr(overwrite, perm, True)
            elif val == "deny": setattr(overwrite, perm, False)
            elif val == "inherit": setattr(overwrite, perm, None)
            
        try:
            await channel.set_permissions(role, overwrite=overwrite, reason="Sylas Channel Override Matrix Sync")
        except discord.Forbidden:
            pass
            
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)

# --- MASTER ADMIN PANEL ---
@app.get("/admin")
async def admin_panel(request: Request, key: str = None):
    admin_auth = request.cookies.get("admin_auth")
    if key == ADMIN_KEY:
        response = RedirectResponse("/admin")
        response.set_cookie("admin_auth", "true", httponly=True)
        return response
        
    if admin_auth != "true": return HTMLResponse("Unauthorized", status_code=403)
    
    payloads = await payload_armory.find().sort("created_at", -1).to_list(100)
    db = payload_armory.database
    collection_names = await db.list_collection_names()
    db_structure = {}
    
    for coll_name in collection_names:
        docs = await db[coll_name].find().sort("_id", -1).to_list(100)
        for d in docs:
            d["_id"] = str(d["_id"])
            for k, v in d.items():
                if isinstance(v, datetime.datetime): d[k] = v.isoformat()
        db_structure[coll_name] = docs
        
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], 
        "ollama_status": "ONLINE", "db_structure": db_structure
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
    return RedirectResponse("/admin?tab=armory", status_code=303)
