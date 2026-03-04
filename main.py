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
            if (await c.get(f"{OLLAMA_URL}/api/tags")).status_code == 200: ollama_status = "ONLINE"
    except: pass

    payloads = await payload_armory.find().sort("created_at", -1).to_list(100)
    
    # SUDO DB FETCH: Dynamically pull every collection and format datetimes safely
    db = payload_armory.database
    collection_names = await db.list_collection_names()
    
    db_structure = {}
    for coll_name in collection_names:
        docs = await db[coll_name].find().sort("_id", -1).to_list(100)
        for d in docs: 
            d["_id"] = str(d["_id"])
            for key, val in d.items():
                if isinstance(val, datetime.datetime):
                    d[key] = val.isoformat()
        db_structure[coll_name] = docs
    
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], 
        "ollama_status": ollama_status, "db_structure": db_structure
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

@app.post("/admin/db/delete_doc/{collection}/{doc_id}")
async def admin_db_delete_doc(request: Request, collection: str, doc_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    db = payload_armory.database
    await db[collection].delete_one({"_id": ObjectId(doc_id)})
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/drop_collection/{collection}")
async def admin_drop_collection(request: Request, collection: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    db = payload_armory.database
    await db[collection].drop()
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/edit_doc/{collection}/{doc_id}")
async def admin_db_edit_doc(request: Request, collection: str, doc_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    form_data = await request.form()
    raw_json = form_data.get("raw_json")
    try:
        parsed = json.loads(raw_json)
        parsed.pop("_id", None) 
        db = payload_armory.database
        await db[collection].update_one({"_id": ObjectId(doc_id)}, {"$set": parsed})
    except Exception as e: print(f"DB Edit Error: {e}")
    return RedirectResponse("/admin?tab=db", status_code=303)

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
    session_user = serializer.loads(user_cookie)
    web_username = session_user.get("username")
    form_data = await request.form()
    guild = bot.get_guild(int(guild_id))
    if guild:
        for r in guild.roles:
            selected_perms = form_data.getlist(f"perms_{r.id}")
            all_discord_perms = [p[0] for p in discord.Permissions()]
            new_kwargs = {perm: (perm in selected_perms) for perm in all_discord_perms}
            try: await r.edit(permissions=discord.Permissions(**new_kwargs), reason=f"Sylas Web Sync (By {web_username})")
            except discord.Forbidden: pass
    return RedirectResponse(f"/server/{guild_id}", status_code=303)

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str):
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
async def mod_action(request: Request, guild_id: str, action: str, target_id: str, tab: str = "users"):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    session_user = serializer.loads(user_cookie)
    web_username = session_user.get("username")
    form_data = await request.form()
    reason = form_data.get("reason", "No reason provided")
    full_reason = f"{reason} (via Web Admin: {web_username})"
    guild = bot.get_guild(int(guild_id))
    msg = "❌ Action failed: Guild not found."
    if guild:
        target = guild.get_member(int(target_id))
        if target:
            if guild.me.top_role <= target.top_role:
                msg = f"❌ Action blocked: The Sylas Bot role is lower than {target.name}'s role. Move my role higher!"
            else:
                try:
                    if action == "kick": 
                        await target.kick(reason=full_reason)
                        msg = f"✅ Kicked {target.name}. Reason: {reason}"
                    elif action == "ban": 
                        await target.ban(reason=full_reason)
                        msg = f"✅ Banned {target.name}. Reason: {reason}"
                    elif action == "timeout": 
                        await target.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=10), reason=full_reason)
                        msg = f"✅ Timed out {target.name} for 10 minutes. Reason: {reason}"
                except discord.Forbidden: msg = "❌ Action blocked: I lack Discord permissions."
                except Exception as e: msg = f"❌ Internal Error: {str(e)}"
        else: msg = "❌ User not found in server."
    safe_msg = urllib.parse.quote(msg)
    return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&msg={safe_msg}", status_code=303)
