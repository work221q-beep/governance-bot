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
    response.delete_cookie("session", path="/")
    response.delete_cookie("admin_auth", path="/")
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
    if not user_cookie: return RedirectResponse("/login")
    
    session_user = serializer.loads(user_cookie)
    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": session_user, "is_master": is_master})

@app.get("/server/{guild_id}")
async def redirect_to_permissions(guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions")

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    roles, users, bots, channels = [], [], [], []
    
    if bot_in_guild:
        for r in reversed(guild.roles):
            roles.append({
                "id": str(r.id), "name": r.name, 
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "is_everyone": r.name == "@everyone", "is_bot": r.managed
            })
            
        for m in guild.members:
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
        "bots": bots, "channels": channels, "guild_name": guild_name,
        "user": session_user, "bot_in_guild": bot_in_guild
    })

@app.post("/server/{guild_id}/action/{action}/{target_id}")
async def mod_action(request: Request, guild_id: str, action: str, target_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    form_data = await request.form()
    custom_reason = form_data.get("reason", "No reason provided.")
    include_name = form_data.get("include_name") == "on"
    timeout_duration = int(form_data.get("duration", 10))
    
    session_user = serializer.loads(user_cookie)
    admin_name = session_user.get('username')
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    target = guild.get_member(int(target_id))
    if not target: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    web_member = guild.get_member(int(session_user.get("id")))
    
    if web_member and guild.owner_id != web_member.id and web_member.top_role <= target.top_role:
        return HTMLResponse(
            f"<div style='background:#09090b; color:white; padding:40px; text-align:center; border: 1px solid #ef4444; border-radius: 10px; max-width: 500px; margin: 50px auto;'>"
            f"<h2 style='color:#ef4444; font-weight: 900;'>Admin Access Denied</h2>"
            f"<p>You cannot {action} a user with an equal or higher role.</p>"
            f"<a href='/server/{guild_id}/permissions' style='color:white; text-decoration:underline;'>Return</a></div>", status_code=403)
            
    if guild.owner_id == target.id or guild.me.top_role <= target.top_role:
        return HTMLResponse(
            f"<div style='background:#09090b; color:white; padding:40px; text-align:center; border: 1px solid #ef4444; border-radius: 10px; max-width: 500px; margin: 50px auto;'>"
            f"<h2 style='color:#ef4444; font-weight: 900;'>Bot Hierarchy Error</h2>"
            f"<p>Sylas cannot {action} <b>{target.name}</b>. The bot's role must be higher than the target's role.</p>"
            f"<a href='/server/{guild_id}/permissions' style='color:white; text-decoration:underline;'>Return</a></div>", status_code=403)
            
    audit_log_reason = f"Sylas Web Admin ({admin_name}): {custom_reason}"
    
    if include_name:
        dm_message = f"You have been **{action}** in **{guild.name}**.\n**Reason:** {custom_reason}\n*Action triggered by Web Admin: {admin_name}*"
    else:
        dm_message = f"You have been **{action}** in **{guild.name}**.\n**Reason:** {custom_reason}"

    if not target.bot:
        try:
            await target.send(dm_message)
        except discord.Forbidden:
            pass
            
    try:
        if action == "kick": 
            await target.kick(reason=audit_log_reason)
        elif action == "ban": 
            await target.ban(reason=audit_log_reason)
        elif action == "timeout": 
            await target.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=timeout_duration), reason=audit_log_reason)
    except discord.Forbidden:
        return HTMLResponse("Bot Permission Error. Ensure Sylas has standard Kick/Ban/Timeout permissions.", status_code=403)
            
    tab = "bots" if target.bot else "users"
    return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}", status_code=303)

@app.post("/server/{guild_id}/channel/{channel_id}/override")
async def channel_override(request: Request, guild_id: str, channel_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    form_data = await request.form()
    role_id = form_data.get("role_id")
    
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    channel = guild.get_channel(int(channel_id))
    role = guild.get_role(int(role_id)) if role_id else guild.default_role
    
    if channel and role:
        overwrite = channel.overwrites_for(role)
        for perm in ["view_channel", "send_messages", "embed_links", "attach_files", "manage_messages"]:
            val = form_data.get(perm)
            if val == "allow": setattr(overwrite, perm, True)
            elif val == "deny": setattr(overwrite, perm, False)
            elif val == "inherit": setattr(overwrite, perm, None)
            
        try:
            await channel.set_permissions(role, overwrite=overwrite, reason="Sylas Channel Override Matrix Sync")
        except discord.Forbidden:
            return HTMLResponse("Access Denied.", status_code=403)
            
    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code=303)

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
        "ai_status": "ONLINE", "db_structure": db_structure
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
