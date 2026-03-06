import os, asyncio, httpx, discord, datetime, json, urllib.parse
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bson import ObjectId
from bot import start_bot, bot, engine_state
from ai import harvest_loop, harvest_payloads, parallel_harvest_sweep
from db import init_indexes, payload_armory, vuln_state, server_configs
from premium import is_guild_premium # NEW: Import Premium verification

app = FastAPI()
templates = Jinja2Templates(directory="templates")
serializer = URLSafeSerializer(os.getenv("SECRET_KEY", "supersecret"))

# Environment Configurations
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
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={encoded_uri}&scope=identify%20guilds"
    return RedirectResponse(url)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("session", path="/")
    response.delete_cookie("admin_auth", path="/")
    return response

@app.get("/invite")
async def invite_bot(guild_id: str = None):
    state = f"invite_{guild_id}" if guild_id else "invite"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions=8&scope=bot&redirect_uri={encoded_uri}&response_type=code&state={state}"
    if guild_id:
        url += f"&guild_id={guild_id}&disable_guild_select=true"
    return RedirectResponse(url)

@app.get("/auth/callback")
async def callback(request: Request, code: str = None, error: str = None, state: str = None):
    if state and state.startswith("invite"):
        if error:
            return RedirectResponse(url="/")
        
        parts = state.split("_")
        if len(parts) > 1 and parts[1]:
            guild_id = parts[1]
            return HTMLResponse(content=f"""
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/server/{guild_id}/permissions" />
                    <style>body {{ background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}</style>
                </head>
                <body><h2 style="font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;">AUTHORIZED. REDIRECTING...</h2></body>
                </html>
            """)
        else:
            return HTMLResponse(content="""
                <html>
                <head>
                    <meta http-equiv="refresh" content="3;url=/" />
                    <style>body { background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }</style>
                </head>
                <body><h2 style="font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;">AUTHORIZED. REDIRECTING...</h2></body>
                </html>
            """)

    if error:
        return RedirectResponse(url="/login")
    if not code:
        return RedirectResponse(url="/")

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
        "id": user["id"], "username": user["username"], "global_name": user.get("global_name"), "avatar": avatar_url, "guilds": manageable_guilds
    }), httponly=True)
    return response

@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    session_user = serializer.loads(user_cookie)
    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    
    # Check premium status for all guilds
    for guild in session_user.get("guilds", []):
        guild["is_premium"] = await is_guild_premium(guild["id"])
        
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": session_user, "is_master": is_master})

@app.get("/server/{guild_id}")
async def redirect_to_permissions(guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions")

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str, tab: str = "roles", error: str = None, error_title: str = None):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    has_premium = await is_guild_premium(guild_id)
    
    user_power = "Moderator"
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            if g.get("owner"):
                user_power = "Owner"
            elif (int(g.get("permissions", 0)) & 0x8) == 0x8:
                user_power = "Administrator"
            break

    roles, users, bots, channels = [], [], [],[]
    
    web_member = guild.get_member(int(session_user.get("id"))) if bot_in_guild else None
    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")
    
    if bot_in_guild:
        for r in reversed(guild.roles):
            current_perms = [perm[0] for perm in r.permissions if perm[1]]
            
            can_edit = True
            if r >= guild.me.top_role and guild.owner_id != guild.me.id:
                can_edit = False
            if r.managed:
                can_edit = False
                
            roles.append({
                "id": str(r.id), "name": r.name, 
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "is_everyone": r.name == "@everyone", "is_bot": r.managed,
                "current": current_perms, "can_edit": can_edit
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
        "user": session_user, "bot_in_guild": bot_in_guild,
        "has_premium": has_premium, "user_power": user_power,
        "display_name": display_name, "user_avatar": user_avatar,
        "active_tab": tab, "error": error, "error_title": error_title
    })

# --- FIX: ADDED MISSING CORE INFRASTRUCTURE SYNC ROUTES ---
@app.get("/server/{guild_id}/sync")
async def sync_manager_get(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    roles =[]
    if bot_in_guild:
        for r in reversed(guild.roles):
            # Parse existing permissions to check boxes correctly
            current_perms = [perm[0] for perm in r.permissions if perm[1]]
            
            can_edit = True
            if r >= guild.me.top_role and guild.owner_id != guild.me.id:
                can_edit = False
            if r.managed:
                can_edit = False
                
            roles.append({
                "id": str(r.id), "name": r.name, 
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "is_everyone": r.name == "@everyone", "is_bot": r.managed,
                "current": current_perms, "can_edit": can_edit
            })

    return templates.TemplateResponse("sync.html", {
        "request": request, "guild_id": guild_id, "guild_name": guild_name,
        "user": session_user, "bot_in_guild": bot_in_guild, "roles": roles
    })

@app.post("/server/{guild_id}/sync")
async def apply_sync_post(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/sync")
    
    session_user = serializer.loads(user_cookie)
    web_member = guild.get_member(int(session_user.get("id")))
    
    if not web_member or (not web_member.guild_permissions.administrator and not web_member.guild_permissions.manage_roles and guild.owner_id != web_member.id):
        return RedirectResponse(f"/server/{guild_id}/permissions?error=You do not have permission to manage roles.&error_title=Access Denied", status_code=303)
    
    form_data = await request.form()
    
    managed_perms = [
        "administrator", "manage_guild", "manage_roles", "manage_channels", 
        "kick_members", "ban_members", "send_messages", "embed_links", 
        "attach_files", "manage_messages", "mention_everyone", "manage_webhooks", 
        "connect", "speak", "mute_members", "move_members", "manage_events", "view_audit_log"
    ]
    
    for role in guild.roles:
        # Safety constraint: Do not edit roles higher than the bot or managed roles
        if role >= guild.me.top_role and guild.owner_id != guild.me.id:
            continue
        if role.managed:
            continue
            
        # If the role was not submitted in the form at all, skip it.
        # We can check this by seeing if a hidden input with the role id was submitted.
        # Wait, we didn't add the hidden input yet. Let's just check if the role is editable.
        # Actually, if the role is editable, it IS in the form.
        # But what if the user unchecks ALL boxes? `perms_list` will be empty.
        # That's fine, we want to clear all permissions.
        
        perms_list = form_data.getlist(f"perms_{role.id}")
        
        # Safely update permissions by creating a new Permissions object
        # First, get all current permissions as a dict
        current_kwargs = {}
        for prop in dir(role.permissions):
            if not prop.startswith('_') and prop != 'value' and isinstance(getattr(type(role.permissions), prop, None), property):
                try:
                    current_kwargs[prop] = getattr(role.permissions, prop)
                except:
                    pass
                    
        # Override with managed permissions from the form
        for p in managed_perms:
            current_kwargs[p] = p in perms_list
            
        try:
            new_perms = discord.Permissions(**current_kwargs)
        except Exception as e:
            print(f"Error creating permissions for {role.name}: {e}")
            new_perms = role.permissions
        
        # Only issue discord API call if permissions actually changed
        if role.permissions.value != new_perms.value:
            try:
                await role.edit(permissions=new_perms, reason="Sylas Web Admin: Bulk Infrastructure Sync")
            except discord.Forbidden:
                return RedirectResponse(f"/server/{guild_id}/permissions?error=Bot lacks permission to edit role {role.name}.&error_title=Bot Permission Error", status_code=303)
            except Exception as e:
                return RedirectResponse(f"/server/{guild_id}/permissions?error=Failed to edit role {role.name}: {str(e)}&error_title=Error", status_code=303)

    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)
# ----------------------------------------------------------

@app.get("/server/{guild_id}/premium")
async def premium_manager(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    # Check database for active sub
    has_premium = await is_guild_premium(int(guild_id))

    user_power = "Moderator"
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            if g.get("owner"):
                user_power = "Owner"
            elif (int(g.get("permissions", 0)) & 0x8) == 0x8:
                user_power = "Administrator"
            break

    web_member = guild.get_member(int(session_user.get("id"))) if bot_in_guild else None
    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")

    return templates.TemplateResponse("premium.html", {
        "request": request, "guild_id": guild_id, "guild_name": guild_name,
        "user": session_user, "has_premium": has_premium,
        "user_power": user_power, "display_name": display_name, "user_avatar": user_avatar
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
    
    tab = "bots" if target and target.bot else "users"
    
    if web_member and guild.owner_id != web_member.id and web_member.top_role <= target.top_role:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=You cannot {action} a user with an equal or higher role.&error_title=Admin Access Denied", status_code=303)
            
    if guild.owner_id == target.id or guild.me.top_role <= target.top_role:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Sylas cannot {action} {target.name}. The bot's role must be higher than the target's role.&error_title=Bot Hierarchy Error", status_code=303)
            
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
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Bot Permission Error. Ensure Sylas has standard Kick/Ban/Timeout permissions.&error_title=Permission Denied", status_code=303)
            
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
        for perm in["view_channel", "send_messages", "embed_links", "attach_files", "manage_messages"]:
            val = form_data.get(perm)
            if val == "allow": setattr(overwrite, perm, True)
            elif val == "deny": setattr(overwrite, perm, False)
            elif val == "inherit": setattr(overwrite, perm, None)
            
        try:
            await channel.set_permissions(role, overwrite=overwrite, reason="Sylas Channel Override Matrix Sync")
        except discord.Forbidden:
            return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas lacks permissions to manage this channel.&error_title=Channel Access Denied", status_code=303)
            
    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code=303)

@app.get("/admin")
async def admin_panel(request: Request, key: str = None):
    admin_auth = request.cookies.get("admin_auth")
    if key == ADMIN_KEY:
        response = RedirectResponse("/admin")
        response.set_cookie("admin_auth", "true", httponly=True)
        return response
    if admin_auth != "true": return HTMLResponse("Unauthorized", status_code=403)
    
    # Sort by raid_type and limit increased to 1000 for the full armory view
    payloads = await payload_armory.find().sort([("raid_type", 1), ("created_at", -1)]).to_list(1000)
    db = payload_armory.database
    collection_names = await db.list_collection_names()
    db_structure = {}
    for coll_name in collection_names:
        docs = await db[coll_name].find().sort("_id", -1).to_list(1000)
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
    bg_tasks.add_task(parallel_harvest_sweep)
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
