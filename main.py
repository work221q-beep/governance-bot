import os, asyncio, httpx, discord, datetime, json, urllib.parse
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bson import ObjectId
from bot import start_bot, bot, engine_state
from ai import harvest_loop, harvest_payloads, parallel_harvest_sweep
from db import init_indexes, payload_armory
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
async def login(next_url: str = None):
    state = f"login_{next_url}" if next_url else "login"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={encoded_uri}&scope=identify%20guilds&state={urllib.parse.quote(state)}"
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
                    <meta http-equiv="refresh" content="3;url=/dashboard" />
                    <style>body { background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }</style>
                </head>
                <body><h2 style="font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;">AUTHORIZED. REDIRECTING...</h2></body>
                </html>
            """)

    if error:
        if state and "premium" in state:
            return RedirectResponse(url="/")
        return RedirectResponse(url="/login")
    if not code:
        if state and "premium" in state:
            return RedirectResponse(url="/")
        return RedirectResponse(url="/login")

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
    
    redirect_url = "/dashboard"
    if state and state.startswith("login_"):
        parts = state.split("_", 1)
        if len(parts) > 1 and parts[1]:
            redirect_url = urllib.parse.unquote(parts[1])
    elif state and state.startswith("invite"):
        redirect_url = "/"
            
    response = RedirectResponse(url=redirect_url)
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
            edit_reason = ""
            
            if r.managed:
                can_edit = False
                edit_reason = "Managed by an integration"
            elif r >= guild.me.top_role and guild.owner_id != guild.me.id:
                can_edit = False
                edit_reason = "Role is higher or equal to the bot's highest role"
            elif user_power not in ["Owner", "Administrator"]:
                if web_member and r >= web_member.top_role and guild.owner_id != web_member.id:
                    can_edit = False
                    edit_reason = "Role is higher or equal to your highest role"
                
            roles.append({
                "id": str(r.id), "name": r.name, 
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "is_everyone": r.name == "@everyone", "is_bot": r.managed,
                "current": current_perms, "can_edit": can_edit,
                "edit_reason": edit_reason
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
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)

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
    if not user_cookie: 
        return RedirectResponse(f"/login?next_url={urllib.parse.quote(f'/server/{guild_id}/premium')}")
    
    session_user = serializer.loads(user_cookie)
    guild = bot.get_guild(int(guild_id))
    
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    # Check database for active sub
    has_premium = await is_guild_premium(int(guild_id))
    
    from db import guild_premium
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    premium_expires_at = prem_doc["expires_at"].isoformat() if prem_doc and "expires_at" in prem_doc else None

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
        "user": session_user, "has_premium": has_premium, "premium_expires_at": premium_expires_at,
        "user_power": user_power, "display_name": display_name, "user_avatar": user_avatar
    })

@app.post("/server/{guild_id}/redeem_key")
async def redeem_key(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    
    form_data = await request.form()
    key = form_data.get("license_key", "").strip()
    
    from premium import redeem_license_key
    success = await redeem_license_key(guild_id, key)
    
    if success:
        return RedirectResponse(f"/server/{guild_id}/premium?success=true", status_code=303)
    else:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid or expired license key.&error_title=Redemption Failed", status_code=303)

@app.post("/server/{guild_id}/buy_premium")
async def buy_premium(request: Request, guild_id: str):
    import httpx, uuid, os
    from db import payments
    
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/login")
    session_user = serializer.loads(user_cookie)
    
    form_data = await request.form()
    plan = form_data.get("plan", "monthly")
    
    amount = 5.00 if plan == "weekly" else 17.99
    days = 7 if plan == "weekly" else 30
    
    order_id = f"SYLAS-{guild_id}-{uuid.uuid4().hex[:8]}"
    base_url = os.getenv("APP_URL", "http://localhost:8000")
    
    async with httpx.AsyncClient() as client:
        payload = {
            "amount": amount,
            "currency": "USD",
            "merchant_wallet": os.getenv("POLYGON_WALLET", "0x0000000000000000000000000000000000000000"),
            "callback_url": f"{base_url}/api/webhooks/payment?chain2pay_order_id={order_id}",
            "success_url": f"{base_url}/server/{guild_id}/premium?success=true",
            "cancel_url": f"{base_url}/server/{guild_id}/premium",
            "customer_email": session_user.get("email", "user@example.com")
        }
        try:
            resp = await client.post("https://chain2pay.cloud/api/generate", json=payload)
            data = resp.json()
            if data.get("success"):
                c2p_order_id = data.get("order_id")
                await payments.insert_one({
                    "internal_order_id": order_id,
                    "chain2pay_order_id": c2p_order_id,
                    "guild_id": guild_id,
                    "user_id": session_user.get("id"),
                    "amount": amount,
                    "days": days,
                    "status": "pending",
                    "ipn_token": data.get("ipn_token"),
                    "created_at": datetime.datetime.utcnow()
                })
                return RedirectResponse(data["payment_url"], status_code=303)
            else:
                return RedirectResponse(f"/server/{guild_id}/premium?error=Payment generation failed: {data.get('error')}", status_code=303)
        except Exception as e:
            return RedirectResponse(f"/server/{guild_id}/premium?error=Payment service unavailable.", status_code=303)

@app.get("/api/webhooks/payment")
async def payment_webhook(request: Request):
    from db import payments
    from premium import generate_license_key, redeem_license_key
    from bot import bot
    
    # The webhook sends chain2pay_order_id, txid_out, value_coin, coin
    c2p_order_id = request.query_params.get("chain2pay_order_id")
    value_coin = request.query_params.get("value_coin")
    txid_out = request.query_params.get("txid_out")
    
    if not c2p_order_id:
        return {"status": "ignored"}
        
    payment = await payments.find_one({"chain2pay_order_id": c2p_order_id})
    if not payment or payment["status"] == "paid":
        return {"status": "ok"}
        
    # Verify amount paid is correct
    paid_amount = float(value_coin) if value_coin else 0.0
    expected_amount = float(payment["amount"])
    
    if paid_amount >= expected_amount * 0.95: # 5% slippage tolerance for crypto
        await payments.update_one({"_id": payment["_id"]}, {"$set": {"status": "paid", "txid_out": txid_out}})
        
        # Generate and redeem key automatically
        key = await generate_license_key(payment["days"])
        await redeem_license_key(payment["guild_id"], key)
        
        # Try to DM user
        try:
            user = await bot.fetch_user(int(payment["user_id"]))
            if user:
                guild = bot.get_guild(int(payment["guild_id"]))
                guild_name = guild.name if guild else "your server"
                await user.send(f"🎉 **Payment Successful!**\n\nYour subscription for **{guild_name}** has been activated.\n**License Key:** `{key}` (Auto-redeemed)\n**Duration:** {payment['days']} Days\n**Transaction ID:** `{txid_out}`\n\nThank you for upgrading to Sylas Premium!")
        except:
            pass
    else:
        print(f"Payment amount mismatch: Expected {expected_amount}, got {paid_amount}")
                
    return {"status": "ok"}

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
    collection_names = [c for c in collection_names if not c.startswith("system.")]
    db_structure = {}
    for coll_name in collection_names:
        docs = await db[coll_name].find().sort("_id", -1).to_list(1000)
        for d in docs:
            d["_id"] = str(d["_id"])
            for k, v in d.items():
                if isinstance(v, datetime.datetime): d[k] = v.isoformat()
        db_structure[coll_name] = docs
        
    # Get all servers the bot is in
    servers = []
    from db import guild_cooldowns
    for guild in bot.guilds:
        is_prem = await is_guild_premium(guild.id)
        # Get active cooldowns for this server
        cds = await guild_cooldowns.find({"guild_id": str(guild.id)}).to_list(100)
        cooldown_modules = [cd["raid_type"] for cd in cds]
        servers.append({
            "id": str(guild.id),
            "name": guild.name,
            "member_count": guild.member_count,
            "is_premium": is_prem,
            "cooldowns": cooldown_modules
        })
        
    # Get license keys
    from db import license_keys, payments
    keys = await license_keys.find({"used": False}).sort("expires_at", -1).to_list(1000)
    for k in keys:
        k["_id"] = str(k["_id"])
        
    # Get payments and revenue
    # Chain2Pay might send "paid" or "completed", so we'll check for "paid"
    all_payments = await payments.find({"status": "paid"}).sort("created_at", -1).to_list(1000)
    total_revenue = sum(float(p.get("amount", 0)) for p in all_payments)
        
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], 
        "ai_status": "ONLINE", "db_structure": db_structure,
        "servers": servers, "license_keys": keys,
        "payments": all_payments, "total_revenue": total_revenue
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

@app.post("/admin/db/drop_collection/{coll_name}")
async def admin_drop_collection(request: Request, coll_name: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    db = payload_armory.database
    await db.drop_collection(coll_name)
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/delete_doc/{coll_name}/{doc_id}")
async def admin_delete_doc(request: Request, coll_name: str, doc_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    db = payload_armory.database
    try:
        await db[coll_name].delete_one({"_id": ObjectId(doc_id)})
    except:
        await db[coll_name].delete_one({"_id": doc_id})
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/edit_doc/{coll_name}/{doc_id}")
async def admin_edit_doc(request: Request, coll_name: str, doc_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    form = await request.form()
    raw_json = form.get("raw_json")
    db = payload_armory.database
    try:
        data = json.loads(raw_json)
        # Remove _id from data to avoid modifying immutable field
        if "_id" in data:
            del data["_id"]
        try:
            await db[coll_name].update_one({"_id": ObjectId(doc_id)}, {"$set": data})
        except:
            await db[coll_name].update_one({"_id": doc_id}, {"$set": data})
    except Exception as e:
        print(f"Error editing doc: {e}")
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/generate_key")
async def admin_generate_key(request: Request):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    form = await request.form()
    days = int(form.get("days", 30))
    from premium import generate_license_key
    await generate_license_key(days)
    return RedirectResponse("/admin?tab=billing", status_code=303)

@app.post("/admin/server/{guild_id}/toggle_premium")
async def admin_toggle_premium(request: Request, guild_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    from premium import is_guild_premium, grant_premium
    from db import guild_premium
    is_prem = await is_guild_premium(int(guild_id))
    if is_prem:
        await guild_premium.delete_one({"guild_id": guild_id})
    else:
        await grant_premium(guild_id, 30) # Default 30 days
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/server/{guild_id}/reset_cooldowns")
async def admin_reset_cooldowns(request: Request, guild_id: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    from db import guild_cooldowns
    await guild_cooldowns.delete_many({"guild_id": guild_id})
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/server/{guild_id}/reset_cooldown/{module}")
async def admin_reset_cooldown(request: Request, guild_id: str, module: str):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    from db import guild_cooldowns
    await guild_cooldowns.delete_one({"guild_id": guild_id, "raid_type": module})
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/gift_premium")
async def admin_gift_premium(request: Request):
    if request.cookies.get("admin_auth") != "true": return RedirectResponse("/")
    form = await request.form()
    guild_id = form.get("guild_id")
    days = int(form.get("days", 30))
    from premium import grant_premium
    if guild_id:
        await grant_premium(guild_id, days)
    return RedirectResponse("/admin?tab=billing", status_code=303)
