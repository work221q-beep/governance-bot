import os, asyncio, httpx, discord, datetime, time, json, urllib.parse, hmac, secrets, re, hashlib
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId
from bot import start_bot, bot, engine_state
from ai import harvest_loop, parallel_harvest_sweep
from db import init_indexes, payload_armory, db
from premium import is_guild_premium

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- DYNAMIC DURATION FORMATTER ---
def format_duration(days_float):
    try:
        days_float = float(days_float)
    except (ValueError, TypeError):
        return "0 Mins"
        
    if days_float >= 365:
        val = days_float / 365.0
        return f"{int(val) if val.is_integer() else round(val, 2)} year"
    elif days_float >= 30:
        val = days_float / 30.0
        return f"{int(val) if val.is_integer() else round(val, 2)} month"
    elif days_float >= 7:
        val = days_float / 7.0
        return f"{int(val) if val.is_integer() else round(val, 2)} week"
    elif days_float >= 1:
        return f"{int(days_float) if days_float.is_integer() else round(days_float, 2)} Days"
    elif days_float >= (1/24.0):
        val = days_float * 24.0
        return f"{int(val) if val.is_integer() else round(val, 2)} Hours"
    else:
        val = days_float * 1440.0
        return f"{int(val) if val.is_integer() else round(val, 2)} Mins"

templates.env.globals["format_duration"] = format_duration
# ----------------------------------

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ADMIN_KEY = os.getenv("ADMIN_KEY")
MASTER_DISCORD_ID = os.getenv("MASTER_DISCORD_ID")

if not ADMIN_KEY or not MASTER_DISCORD_ID:
    raise RuntimeError("CRITICAL: ADMIN_KEY and MASTER_DISCORD_ID environment variables must be set.")

ALLOWED_COLLECTIONS = ["payload_armory", "guild_premium", "guild_cooldowns", "license_keys", "payments", "gift_logs", "sessions", "audit_logs", "admin_sessions", "premium_gifts"]

app_state = {
    "payments_active": True,
    "redemption_active": True,
    "maintenance_mode": "none"
}

def validate_object_id(doc_id: str) -> ObjectId:
    if not re.match(r'^[a-fA-F0-9]{24}$', doc_id): raise HTTPException(status_code=400, detail="Invalid ID format")
    return ObjectId(doc_id)

def secure_csrf_check(form_data_token: str, session_token: str) -> bool:
    if not session_token or not form_data_token: return False
    return hmac.compare_digest(form_data_token, session_token)

async def get_reliable_member(guild, user_id: int):
    member = guild.get_member(user_id)
    if not member:
        try: member = await guild.fetch_member(user_id)
        except discord.NotFound: return None
    return member

@app.middleware("http")
async def global_middleware(request: Request, call_next):
    path = request.url.path
    if app_state["maintenance_mode"] in ["web", "both"]:
        if not path.startswith("/admin") and not path.startswith("/api/health") and not path.startswith("/static"):
            return HTMLResponse(
                "<html><body style='background:#030305;color:#ff003c;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;text-align:center;'>"
                "<h1 style='font-size:3rem;margin-bottom:10px;'>UPLINK SEVERED</h1>"
                "<p style='color:#f4f4f5;font-size:1.2rem;opacity:0.7;'>The web matrix is currently undergoing structural maintenance. Return shortly.</p>"
                "</body></html>", status_code=503
            )
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    return HTMLResponse("OK", status_code=200)

async def keep_awake_loop():
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    target_url = f"{base_url}/api/health"
    while True:
        await asyncio.sleep(10 * 60)
        try:
            async with httpx.AsyncClient() as client: await client.get(target_url, timeout=10.0)
        except Exception: pass
        try:
            now = datetime.datetime.utcnow()
            await db.sessions.delete_many({"expires_at": {"$lt": now}})
            await db.admin_sessions.delete_many({"expires_at": {"$lt": now}})
        except Exception as e: print(f"[Cleanup Error] {e}")

async def payment_reconciliation_loop():
    from db import payments
    await asyncio.sleep(10)
    while True:
        try:
            yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
            grace_period = datetime.datetime.utcnow() - datetime.timedelta(seconds=30)
            stuck_payments = await payments.find({
                "status": "pending",
                "paymento_token": {"$exists": True},
                "created_at": {"$gt": yesterday, "$lt": grace_period}
            }).to_list(50)
            for payment in stuck_payments:
                await verify_and_fulfill_payment(payment["paymento_token"])
                await asyncio.sleep(1) 
        except Exception as e:
            print(f"[Reconciliation Daemon] Error: {e}")
        await asyncio.sleep(30) 

async def verify_and_fulfill_payment(token: str):
    from db import payments, license_keys
    from premium import generate_license_key
    paymento_api_key = os.getenv("PAYMENTO_API_KEY")
    if not paymento_api_key: return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            verify_resp = await client.post("https://api.paymento.io/v1/payment/verify", headers={
                "Api-key": paymento_api_key, "Content-Type": "application/json"
            }, json={"token": token})
            verify_data = verify_resp.json()
            if verify_data.get("success") and "body" in verify_data:
                trusted_order_id = verify_data["body"].get("orderId")
                if not trusted_order_id: return None
                payment = await payments.find_one({"internal_order_id": trusted_order_id})
                if not payment: return None
                if payment["status"] == "paid":
                    return await license_keys.find_one({"internal_order_id": trusted_order_id, "used": False})
                update_result = await payments.update_one(
                    {"_id": payment["_id"], "status": "pending"}, 
                    {"$set": {"status": "paid"}}
                )
                if update_result.modified_count == 1:
                    key = await generate_license_key(payment["days"])
                    await license_keys.update_one(
                        {"key": key},
                        {"$set": {
                            "used": False, "purchased_by": str(payment.get("user_id", "")),
                            "purchased_by_username": payment.get("username", "Unknown"), 
                            "internal_order_id": payment["internal_order_id"],
                            "duration_days": payment["days"], "acknowledged": False, "shown_count": 0
                        }}
                    )
                    return await license_keys.find_one({"key": key})
        except Exception as e: print(f"Payment Verification Failed: {e}")
    return None

async def jit_payment_reconciliation(user_id: str):
    from db import payments
    pending = await payments.find({"user_id": user_id, "status": "pending", "paymento_token": {"$exists": True}}).to_list(5)
    for p in pending: await verify_and_fulfill_payment(p["paymento_token"])

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(harvest_loop())
    asyncio.create_task(keep_awake_loop())
    asyncio.create_task(payment_reconciliation_loop())

@app.get("/")
async def home(request: Request):
    session_id = request.cookies.get("session_id")
    user = None
    if session_id:
        session_doc = await db.sessions.find_one({"session_id": session_id})
        if session_doc: user = session_doc["user"]
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login")
async def login(request: Request, next_url: str = None):
    if next_url and not re.match(r'^/[^/][a-zA-Z0-9_\-\?&=/]*$', next_url): next_url = None
    oauth_state = secrets.token_urlsafe(32)
    encoded_next_url = urllib.parse.quote(next_url, safe="") if next_url else "none"
    state = f"login_{encoded_next_url}_{oauth_state}"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={encoded_uri}&scope=identify%20guilds&state={urllib.parse.quote(state)}"
    response = RedirectResponse(url)
    response.set_cookie("oauth_state", oauth_state, httponly=True, secure=True, max_age=300)
    return response

@app.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id: await db.sessions.delete_one({"session_id": session_id})
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_id", path="/")
    response.delete_cookie("admin_auth", path="/")
    return response

@app.get("/invite")
async def invite_bot(guild_id: str = None):
    state = f"invite_{guild_id}" if guild_id else "invite"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions=8&scope=bot&redirect_uri={encoded_uri}&response_type=code&state={state}"
    if guild_id: url += f"&guild_id={guild_id}&disable_guild_select=true"
    return RedirectResponse(url)

@app.get("/auth/callback")
async def callback(request: Request, code: str = None, error: str = None, state: str = None):
    if state and state.startswith("invite"):
        if error: return RedirectResponse(url="/")
        parts = state.split("_")
        if len(parts) > 1 and parts[1] and parts[1].isdigit():
            guild_id = parts[1]
            return HTMLResponse(content=f"<html><head><meta http-equiv='refresh' content='3;url=/server/{guild_id}/permissions' /><style>body {{ background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}</style></head><body><h2 style='font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;'>AUTHORIZED. REDIRECTING...</h2></body></html>")
        else:
            return HTMLResponse(content="<html><head><meta http-equiv='refresh' content='3;url=/dashboard' /><style>body { background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }</style></head><body><h2 style='font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;'>AUTHORIZED. REDIRECTING...</h2></body></html>")

    if error or not code: return RedirectResponse(url="/login")

    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data={
            "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI
        })
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token: return RedirectResponse(url="/login?error=auth_failed")
            
        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        if user_res.status_code != 200: return RedirectResponse(url="/login?error=discord_api_failure")
            
        user = user_res.json()
        guilds = (await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})).json()

    manageable_guilds = []
    for g in guilds:
        try:
            perms_str = str(g.get("permissions", "0"))
            if len(perms_str) > 20: continue 
            if g.get("owner") or (int(perms_str) & 0x8) == 0x8: manageable_guilds.append(g)
        except (ValueError, TypeError): continue
            
    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png" if user.get("avatar") else None
    
    redirect_url = "/dashboard"
    if state and state.startswith("login_"):
        parts = state.split("_", 2)
        expected_state = request.cookies.get("oauth_state")
        if len(parts) != 3 or parts[2] != expected_state: return RedirectResponse(url="/login?error=csrf_validation_failed")
        if parts[1] != "none":
            parsed_url = urllib.parse.unquote(parts[1])
            if re.match(r'^/[^/][a-zA-Z0-9_\-\?&=/]*$', parsed_url): redirect_url = parsed_url
    elif state and state.startswith("invite"): 
        redirect_url = "/"
            
    response = RedirectResponse(url=redirect_url)
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    user_data = { "id": user["id"], "username": user["username"], "global_name": user.get("global_name"), "avatar": avatar_url, "guilds": manageable_guilds }
    
    await db.sessions.insert_one({ "session_id": session_id, "user": user_data, "csrf_token": csrf_token, "created_at": datetime.datetime.utcnow(), "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=7) })
    await jit_payment_reconciliation(user_data["id"]) 
    response.set_cookie("session_id", session_id, httponly=True, secure=True, samesite="lax", max_age=7*24*60*60)
    response.delete_cookie("oauth_state", path="/")
    return response

async def get_session_user(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id: return None, None
    session_doc = await db.sessions.find_one({"session_id": session_id})
    if not session_doc: return None, None
    return session_doc["user"], session_doc.get("csrf_token")

@app.post("/api/keys/acknowledge")
async def acknowledge_key(request: Request):
    session_user, _ = await get_session_user(request)
    if not session_user: return HTMLResponse(status_code=401)
    data = await request.json()
    from db import license_keys
    await license_keys.update_one({"key": data.get("key"), "purchased_by": str(session_user["id"])}, {"$set": {"acknowledged": True}})
    return HTMLResponse("OK")

@app.post("/api/keys/mark_shown")
async def mark_key_shown(request: Request):
    session_user, _ = await get_session_user(request)
    if not session_user: return HTMLResponse(status_code=401)
    data = await request.json()
    from db import license_keys
    await license_keys.update_one({"key": data.get("key"), "purchased_by": str(session_user["id"])}, {"$inc": {"shown_count": 1}})
    return HTMLResponse("OK")

@app.post("/user/redeem_universal")
async def redeem_universal(request: Request):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    if not app_state["redemption_active"]: return RedirectResponse("/dashboard?error=Key redemption is temporarily disabled for maintenance.")
    
    form_data = await request.form()
    guild_id = form_data.get("guild_id")
    key = form_data.get("license_key")
    
    is_admin = False
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            if g.get("owner") or (int(g.get("permissions", 0)) & 0x8) == 0x8: is_admin = True
            break
            
    if not is_admin: return RedirectResponse("/dashboard?error=You lack admin permissions for that server.")
        
    guild = bot.get_guild(int(guild_id))
    if not guild:
        referer = request.headers.get("referer", "/dashboard")
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(f"{referer}{sep}bot_missing_guild={guild_id}", status_code=303)

    from premium import redeem_license_key
    from db import license_keys
    success = await redeem_license_key(guild_id, key)
    
    if success: 
        await license_keys.update_one({"key": key}, {"$set": {"used_by_user": str(session_user.get("id")), "used_by_username": session_user.get("username", "Unknown"), "acknowledged": True}})
        return RedirectResponse(f"/server/{guild_id}/premium?success=true", status_code=303)
    else: 
        return RedirectResponse("/dashboard?error=Key is invalid or already consumed.", status_code=303)

@app.get("/dashboard")
async def dashboard(request: Request):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    await jit_payment_reconciliation(session_user.get("id"))
    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    from db import payments, license_keys
    user_payments = await payments.find({"user_id": session_user.get("id")}).sort("created_at", -1).to_list(50)
    user_keys = await license_keys.find({"purchased_by": str(session_user.get("id")), "used": False}).sort("_id", -1).to_list(50)
    unacknowledged_key = await license_keys.find_one({"purchased_by": str(session_user.get("id")), "used": False, "acknowledged": False})
    
    for guild in session_user.get("guilds", []): guild["is_premium"] = await is_guild_premium(guild["id"])
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": session_user, "is_master": is_master,
        "user_payments": user_payments, "user_keys": user_keys,
        "unacknowledged_key": unacknowledged_key, "csrf_token": csrf_token
    })

@app.get("/server/{guild_id}")
async def redirect_to_permissions(guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions")

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str, tab: str = "roles", error: str = None, error_title: str = None):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    await jit_payment_reconciliation(session_user.get("id"))
        
    guild = bot.get_guild(int(guild_id))
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    has_premium = await is_guild_premium(guild_id)
    user_power = "Moderator"
    is_authorized = False
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            perms_str = str(g.get("permissions", "0"))
            if g.get("owner"): user_power = "Owner"; is_authorized = True
            elif (int(perms_str) & 0x8) == 0x8: user_power = "Administrator"; is_authorized = True
            break
            
    if not is_authorized: return RedirectResponse("/dashboard?error=You+do+not+have+permission+to+access+this+server.")
        
    roles, users, bots, channels = [], [], [], []
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if bot_in_guild else None

    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")
    
    from db import payments, guild_premium, license_keys
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    guild_payments = await payments.find({"guild_id": str(guild_id), "$or": [{"status": "paid"}, {"status": "pending", "created_at": {"$gt": yesterday}}]}).sort("created_at", -1).to_list(100)
    
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    # FIXED: Added "Z" for correct client side parsing
    premium_expires_at = prem_doc["expires_at"].isoformat() + "Z" if prem_doc and "expires_at" in prem_doc else None
    
    unacknowledged_key = await license_keys.find_one({"purchased_by": str(session_user.get("id")), "used": False, "acknowledged": False})
    
    if bot_in_guild:
        for r in reversed(guild.roles):
            current_perms = [perm[0] for perm in r.permissions if perm[1]]
            can_edit = True
            edit_reason = ""
            if r.managed: can_edit = False; edit_reason = "Managed by an integration"
            elif r >= guild.me.top_role and guild.owner_id != guild.me.id: can_edit = False; edit_reason = "Role is higher or equal to the bot's highest role"
            elif user_power not in ["Owner", "Administrator"]:
                if web_member and r >= web_member.top_role and guild.owner_id != web_member.id: can_edit = False; edit_reason = "Role is higher or equal to your highest role"
            roles.append({ "id": str(r.id), "name": r.name, "color": str(r.color) if r.color.value != 0 else "#71717a", "is_everyone": r.name == "@everyone", "is_bot": r.managed, "current": current_perms, "can_edit": can_edit, "edit_reason": edit_reason })
            
        for m in guild.members:
            avatar = str(m.display_avatar.url) if m.display_avatar else None
            member_data = { "id": str(m.id), "name": m.name, "display_name": m.display_name, "avatar": avatar, "top_role": m.top_role.name if m.top_role else "None" }
            if m.bot: bots.append(member_data)
            else: users.append(member_data)
                
        sorted_channels = []
        for category, channels_in_cat in guild.by_category():
            if category: sorted_channels.append(category)
            sorted_channels.extend(sorted(channels_in_cat, key=lambda c: c.position))
            
        for c in sorted_channels: channels.append({"id": str(c.id), "name": c.name, "type": str(c.type)})

    latest_key = None
    if request.query_params.get('success') == "true":
        latest_key_doc = await license_keys.find_one({"used_by_guild": str(guild_id), "used": True}, sort=[("expires_at", -1)])
        if latest_key_doc: latest_key = {"duration_days": latest_key_doc.get("duration_days", 0)}

    return templates.TemplateResponse("permissions.html", {
        "request": request, "guild_id": guild_id, "roles": roles, "users": users, 
        "bots": bots, "channels": channels, "guild_name": guild_name, "user": session_user, "bot_in_guild": bot_in_guild,
        "has_premium": has_premium, "user_power": user_power, "display_name": display_name, "user_avatar": user_avatar,
        "guild_payments": guild_payments, "premium_expires_at": premium_expires_at, "active_tab": tab, "error": error, "error_title": error_title,
        "csrf_token": csrf_token, "latest_key": latest_key, "unacknowledged_key": unacknowledged_key
    })

@app.get("/server/{guild_id}/sync")
async def sync_manager_get(request: Request, guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)

@app.post("/server/{guild_id}/sync")
async def apply_sync_post(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id): raise HTTPException(status_code=403, detail="Permission denied")
    managed_perms = ["administrator", "manage_guild", "manage_roles", "manage_channels", "kick_members", "ban_members", "send_messages", "embed_links", "attach_files", "manage_messages", "mention_everyone", "manage_webhooks", "connect", "speak", "mute_members", "move_members", "manage_events", "view_audit_log"]
    
    for role in guild.roles:
        if role >= guild.me.top_role and guild.owner_id != guild.me.id: continue
        if role.managed: continue
        if web_member and guild.owner_id != web_member.id and role >= web_member.top_role: continue
        perms_list = form_data.getlist(f"perms_{role.id}")
        current_kwargs = {}
        for prop in dir(role.permissions):
            if not prop.startswith('_') and prop != 'value' and isinstance(getattr(type(role.permissions), prop, None), property):
                try: current_kwargs[prop] = getattr(role.permissions, prop)
                except: pass
        for p in managed_perms: current_kwargs[p] = p in perms_list
        try: new_perms = discord.Permissions(**current_kwargs)
        except Exception: new_perms = role.permissions
        if role.permissions.value != new_perms.value:
            try: 
                await role.edit(permissions=new_perms, reason="Sylas Web Admin: Bulk Infrastructure Sync")
                await asyncio.sleep(0.3)
            except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?error={urllib.parse.quote(f'Bot lacks permission to edit role {role.name}.')}&error_title=Bot Permission Error", status_code=303)
            # FIX: Secured the f-string variable by properly closing the brace
            except Exception as e: return RedirectResponse(f"/server/{guild_id}/permissions?error={urllib.parse.quote(f'Failed to edit role {role.name}: {str(e)[:100]}')}&error_title=Error", status_code=303)
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)

@app.get("/server/{guild_id}/premium")
async def premium_manager(request: Request, guild_id: str, success: str = None, error: str = None):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse(f"/login?next_url={urllib.parse.quote(f'/server/{guild_id}/premium')}")
    await jit_payment_reconciliation(session_user.get("id"))
    guild = bot.get_guild(int(guild_id))
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    has_premium = await is_guild_premium(int(guild_id))
    from db import guild_premium, license_keys
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    
    # FIXED: Added "Z" for correct client side parsing
    premium_expires_at = prem_doc["expires_at"].isoformat() + "Z" if prem_doc and "expires_at" in prem_doc else None

    unacknowledged_key = await license_keys.find_one({"purchased_by": str(session_user.get("id")), "used": False, "acknowledged": False})

    latest_key = None
    if success == "true":
        latest_key_doc = await license_keys.find_one({"used_by_guild": str(guild_id), "used": True}, sort=[("expires_at", -1)])
        if latest_key_doc: latest_key = {"duration_days": latest_key_doc.get("duration_days", 0)}

    payment_return = request.query_params.get("payment_return")
    token = request.query_params.get("token")
    status = request.query_params.get("status")
    payment_state = "none"
    generated_key = None

    if payment_return == "true":
        if status == "5": payment_state = "canceled"
        elif token:
            key_doc = await verify_and_fulfill_payment(token)
            if key_doc:
                payment_state = "paid"
                generated_key = {"key": key_doc["key"], "duration_days": key_doc.get("duration_days", 0)}
            else: payment_state = "pending"

    user_power = "Moderator"
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            if g.get("owner"): user_power = "Owner"
            elif (int(g.get("permissions", 0)) & 0x8) == 0x8: user_power = "Administrator"
            break

    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if bot_in_guild else None
    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")

    return templates.TemplateResponse("premium.html", {
        "request": request, "guild_id": guild_id, "guild_name": guild_name,
        "user": session_user, "has_premium": has_premium, "premium_expires_at": premium_expires_at,
        "user_power": user_power, "display_name": display_name, "user_avatar": user_avatar,
        "csrf_token": csrf_token, "latest_key": latest_key, "success": success,
        "payment_state": payment_state, "generated_key": generated_key, "unacknowledged_key": unacknowledged_key
    })

@app.post("/server/{guild_id}/buy_premium")
async def buy_premium(request: Request, guild_id: str):
    if not app_state["payments_active"]: return RedirectResponse(f"/server/{guild_id}/premium?error=Payment gateway is currently undergoing maintenance.", status_code=303)
    import uuid
    from db import payments
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    guild = bot.get_guild(int(guild_id))
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if guild else None
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id): raise HTTPException(status_code=403, detail="Permission denied")
    plan = form_data.get("plan", "monthly")
    amount = 5.00 if plan == "weekly" else (190.00 if plan == "yearly" else 17.99)
    days = 7 if plan == "weekly" else (365 if plan == "yearly" else 30)
    order_id = f"SYLAS-{guild_id}-{uuid.uuid4().hex[:8]}"
    await payments.insert_one({"internal_order_id": order_id, "guild_id": guild_id, "user_id": session_user.get("id"), "username": session_user.get("username", "Unknown"), "amount": amount, "days": days, "status": "pending", "created_at": datetime.datetime.utcnow()})
    base_url = os.getenv("BASE_URL")
    if not base_url: raise HTTPException(status_code=500, detail="CRITICAL: BASE_URL environment variable is missing.")
    base_url = base_url.rstrip('/')
    paymento_api_key = os.getenv("PAYMENTO_API_KEY")
    if not paymento_api_key: return RedirectResponse(f"/server/{guild_id}/premium?error=Payment gateway API Key missing.", status_code=303)
    async with httpx.AsyncClient() as client:
        try:
            return_url = f"{base_url}/server/{guild_id}/premium?payment_return=true"
            resp = await client.post("https://api.paymento.io/v1/payment/request", headers={"Api-key": paymento_api_key, "Content-Type": "application/json", "Accept": "text/plain"}, json={"fiatAmount": str(amount), "fiatCurrency": "USD", "ReturnUrl": return_url, "orderId": order_id, "Speed": 1, "EmailAddress": "admin@sylas.ai"})
            data = resp.json()
            if data.get("success"):
                token = data.get("body")
                payment_url = f"https://app.paymento.io/gateway?token={token}"
                await payments.update_one({"internal_order_id": order_id}, {"$set": {"paymento_token": token}})
                if payment_url: return RedirectResponse(payment_url, status_code=303)
        except Exception as e: print(f"Paymento API Error: {e}")
    return RedirectResponse(f"/server/{guild_id}/premium?error=Payment gateway unavailable. Try again later.", status_code=303)

async def process_payment_bg(token: str): await verify_and_fulfill_payment(token)

@app.post("/api/webhook/paymento")
async def paymento_webhook(request: Request, bg_tasks: BackgroundTasks):
    raw_body = await request.body()
    signature = (request.headers.get("hmac_sha256_signature") or request.headers.get("x-hmac-sha256-signature") or request.headers.get("hmac-sha256-signature"))
    secret_key = os.getenv("PAYMENTO_SECRET_KEY", "")
    if not secret_key or not signature: return HTMLResponse("Missing Authentication", status_code=403)
    calculated_signature = hmac.new(secret_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest().upper()
    if not hmac.compare_digest(calculated_signature, signature.upper()): return HTMLResponse("Invalid HMAC Signature", status_code=403)
    try: payload = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError: return HTMLResponse("Invalid JSON", status_code=400)
    token = payload.get("Token") or payload.get("token")
    order_status = payload.get("OrderStatus") or payload.get("orderStatus")
    if str(order_status) == "7" and token:
        bg_tasks.add_task(process_payment_bg, token)
        return HTMLResponse("Accepted for background processing", status_code=200)
    return HTMLResponse(f"Ignored Status: {order_status}", status_code=200)

@app.post("/server/{guild_id}/redeem_key")
async def redeem_key(request: Request, guild_id: str):
    if not app_state["redemption_active"]: return RedirectResponse(f"/server/{guild_id}/premium?error=Key redemption is currently disabled for maintenance.", status_code=303)
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    guild = bot.get_guild(int(guild_id))
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if guild else None
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id): raise HTTPException(status_code=403, detail="Permission denied")
    if not hasattr(app.state, 'redeem_rl'): app.state.redeem_rl = {}
    now = time.time()
    user_id = session_user.get("id")
    if len(app.state.redeem_rl) > 1000: app.state.redeem_rl = {k: v for k, v in app.state.redeem_rl.items() if now - v < 5}
    last_attempt = app.state.redeem_rl.get(user_id, 0)
    if now - last_attempt < 5: return RedirectResponse(f"/server/{guild_id}/premium?error=Please wait 5 seconds before trying again.&error_title=Rate Limited", status_code=303)
    app.state.redeem_rl[user_id] = now
    key = form_data.get("license_key", "").strip()
    if not re.match(r'^SYLAS-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$', key): return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid key format.&error_title=Failed", status_code=303)
    from db import license_keys
    key_doc = await license_keys.find_one({"key": key, "used": False})
    if not key_doc: return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid or consumed license key.&error_title=Redemption Failed", status_code=303)
    from premium import redeem_license_key
    success = await redeem_license_key(guild_id, key)
    if success: 
        await license_keys.update_one({"key": key}, {"$set": {"used_by_user": str(user_id), "used_by_username": session_user.get("username", "Unknown"), "acknowledged": True}})
        return RedirectResponse(f"/server/{guild_id}/premium?success=true", status_code=303)
    else: return RedirectResponse(f"/server/{guild_id}/premium?error=Error redeeming license key.&error_title=Redemption Failed", status_code=303)

@app.post("/server/{guild_id}/action/{action}/{target_id}")
async def mod_action(request: Request, guild_id: str, action: str, target_id: str):
    if not target_id.isdigit(): return RedirectResponse(f"/server/{guild_id}/permissions?error=Invalid+Target+ID", status_code=303)
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    custom_reason = form_data.get("reason", "No reason provided.")
    include_name = form_data.get("include_name") == "on"
    timeout_duration = int(form_data.get("duration", 10))
    admin_name = session_user.get('username')
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    target = guild.get_member(int(target_id))
    if not target: return RedirectResponse(f"/server/{guild_id}/permissions")
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member: return RedirectResponse(f"/server/{guild_id}/permissions")
    has_perm = False
    if action == "ban" and web_member.guild_permissions.ban_members: has_perm = True
    elif action == "kick" and web_member.guild_permissions.kick_members: has_perm = True
    elif action == "timeout" and web_member.guild_permissions.moderate_members: has_perm = True
    elif web_member.guild_permissions.administrator or guild.owner_id == web_member.id: has_perm = True
    if not has_perm: return RedirectResponse(f"/server/{guild_id}/permissions?error=You lack the required permissions to perform this action.&error_title=Access Denied", status_code=303)
    tab = "bots" if target and target.bot else "users"
    if web_member and guild.owner_id != web_member.id and web_member.top_role <= target.top_role: return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=You cannot {action} a user with an equal or higher role.&error_title=Admin Access Denied", status_code=303)
    bot_has_perm = False
    if action == "ban" and guild.me.guild_permissions.ban_members: bot_has_perm = True
    elif action == "kick" and guild.me.guild_permissions.kick_members: bot_has_perm = True
    elif action == "timeout" and guild.me.guild_permissions.moderate_members: bot_has_perm = True
    elif guild.me.guild_permissions.administrator: bot_has_perm = True
    if not bot_has_perm: return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Sylas lacks permissions to perform this action. Check bot settings.&error_title=Bot Permission Error", status_code=303)
    if guild.owner_id == target.id or guild.me.top_role <= target.top_role: return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Sylas cannot {action} {target.name}. The bot's role must be higher than the target's role.&error_title=Bot Hierarchy Error", status_code=303)
    audit_log_reason = f"Sylas Web Admin ({admin_name}): {custom_reason}"
    from db import db
    await db.audit_logs.insert_one({ "action": action, "guild_id": guild_id, "target_id": target_id, "admin_id": session_user.get("id"), "reason": custom_reason, "timestamp": datetime.datetime.utcnow() })
    dm_message = f"You have been **{action}** in **{guild.name}**.\n**Reason:** {custom_reason}" + (f"\n*Action triggered by Web Admin: {admin_name}*" if include_name else "")
    if not target.bot:
        try: await target.send(dm_message)
        except discord.Forbidden: pass
    try:
        if action == "kick": await target.kick(reason=audit_log_reason)
        elif action == "ban": await target.ban(reason=audit_log_reason)
        elif action == "timeout": 
            if timeout_duration > 40320 or timeout_duration < 1: raise ValueError("Duration exceeds 28-day API bounds")
            await target.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=timeout_duration), reason=audit_log_reason)
    except (discord.Forbidden, discord.HTTPException, ValueError) as e: 
        error_safe = urllib.parse.quote(str(e)[:150])
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Execution Failed: {error_safe}&error_title=Execution Failed", status_code=303)
    return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}", status_code=303)

@app.post("/server/{guild_id}/channel/{channel_id}/override")
async def channel_override(request: Request, guild_id: str, channel_id: str):
    if not channel_id.isdigit(): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+Channel+ID", status_code=303)
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    role_id = form_data.get("role_id")
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    channel = guild.get_channel(int(channel_id))
    role = guild.get_role(int(role_id)) if role_id and role_id.isdigit() else guild.default_role
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    has_override_auth = False
    if web_member:
        has_override_auth = (web_member.guild_permissions.administrator or (web_member.guild_permissions.manage_channels and web_member.guild_permissions.manage_roles) or guild.owner_id == web_member.id)
    if not web_member or not has_override_auth: raise HTTPException(status_code=403, detail="Permission denied. Missing Manage Roles.")
    if web_member and guild.owner_id != web_member.id and web_member.top_role <= role: return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You cannot edit channel permissions for a role equal to or higher than your own.&error_title=Hierarchy Error", status_code=303)
    if channel and role:
        overwrite = channel.overwrites_for(role)
        extended_perms = [ "view_channel", "send_messages", "embed_links", "attach_files", "manage_messages", "read_message_history", "mention_everyone", "use_external_emojis", "add_reactions", "connect", "speak", "mute_members", "deafen_members", "move_members", "use_voice_activation", "request_to_speak", "manage_events", "send_messages_in_threads", "create_public_threads", "create_private_threads", "manage_threads" ]
        for perm in extended_perms:
            val = form_data.get(perm)
            if val == "allow": setattr(overwrite, perm, True)
            elif val == "deny": setattr(overwrite, perm, False)
            elif val == "inherit": setattr(overwrite, perm, None)
        try: await channel.set_permissions(role, overwrite=overwrite, reason="Sylas Channel Override Matrix Sync")
        except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas lacks permissions to manage this channel.&error_title=Channel Access Denied", status_code=303)
    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code=303)

@app.post("/server/{guild_id}/channel/create")
async def create_channel(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    channel_name = form_data.get("channel_name")
    channel_type = form_data.get("channel_type")
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or (not web_member.guild_permissions.administrator and not web_member.guild_permissions.manage_channels and guild.owner_id != web_member.id): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You do not have permission to manage channels.&error_title=Access Denied", status_code=303)
    try:
        if channel_type == "text": await guild.create_text_channel(name=channel_name, reason="Sylas Web Admin: Channel Created")
        elif channel_type == "voice": await guild.create_voice_channel(name=channel_name, reason="Sylas Web Admin: Channel Created")
        elif channel_type == "category": await guild.create_category(name=channel_name, reason="Sylas Web Admin: Category Created")
    except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas lacks permissions to create channels.&error_title=Permission Denied", status_code=303)
    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code=303)

@app.post("/server/{guild_id}/channel/{channel_id}/delete")
async def delete_channel(request: Request, guild_id: str, channel_id: str):
    if not channel_id.isdigit(): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+Channel+ID", status_code=303)
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or (not web_member.guild_permissions.administrator and not web_member.guild_permissions.manage_channels and guild.owner_id != web_member.id): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You do not have permission to manage channels.&error_title=Access Denied", status_code=303)
    channel = guild.get_channel(int(channel_id))
    if channel:
        try: await channel.delete(reason="Sylas Web Admin: Channel Deleted")
        except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas lacks permissions to delete this channel.&error_title=Permission Denied", status_code=303)
    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code=303)

@app.post("/server/{guild_id}/channel/{channel_id}/rename")
async def rename_channel(request: Request, guild_id: str, channel_id: str):
    if not channel_id.isdigit(): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+Channel+ID", status_code=303)
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    safe_csrf = csrf_token or "invalid_token"
    if not hmac.compare_digest(form_data.get("csrf_token", ""), safe_csrf): raise HTTPException(status_code=403, detail="CSRF token mismatch")
    new_name = form_data.get("new_name")
    if not new_name or len(new_name) < 1 or len(new_name) > 100: return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel name must be between 1 and 100 characters.&error_title=Invalid Name", status_code=303)
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel names can only contain alphanumeric characters, dashes, and underscores.&error_title=Invalid Name", status_code=303)
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or (not web_member.guild_permissions.administrator and not web_member.guild_permissions.manage_channels and guild.owner_id != web_member.id): return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You do not have permission to manage channels.&error_title=Access Denied", status_code=303)
    channel = guild.get_channel(int(channel_id))
    if channel:
        try: await channel.edit(name=new_name, reason="Sylas Web Admin: Channel Renamed")
        except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas lacks permissions to rename this channel.&error_title=Permission Denied", status_code=303)
    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code=303)

@app.get("/admin")
async def admin_panel(request: Request):
    key_param = request.query_params.get("key")
    if key_param and hmac.compare_digest(key_param, ADMIN_KEY):
        response = RedirectResponse("/admin", status_code=303)
        token = secrets.token_urlsafe(32)
        from db import db
        await db.admin_sessions.insert_one({ "token": token, "created_at": datetime.datetime.utcnow(), "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=1) })
        response.set_cookie("admin_auth", token, httponly=True, secure=True, samesite="Strict", max_age=86400)
        return response

    admin_auth = request.cookies.get("admin_auth")
    from db import db, guild_premium
    session = None
    if admin_auth: session = await db.admin_sessions.find_one({ "token": admin_auth, "expires_at": {"$gt": datetime.datetime.utcnow()} })
    if not session:
        response = HTMLResponse(
            "<html><body style='background:#030305;color:#f00;font-family:monospace;display:flex;justify-content:center;align-items:center;height:100vh;flex-direction:column;'>"
            "<h2 style='margin-bottom:20px;letter-spacing:0.2em;'>MASTER UPLINK</h2>"
            "<form method='post' action='/admin/auth'><input type='password' name='key' placeholder='Enter Authorization Key' style='background:#111;border:1px solid #f00;color:#f00;padding:12px;font-size:16px;'><button style='background:#f00;color:#000;padding:12px 20px;border:none;cursor:pointer;font-weight:bold;margin-left:10px;'>Initialize</button></form>"
            "</body></html>", status_code=401
        )
        if admin_auth: response.delete_cookie("admin_auth", path="/")
        return response
    
    from db import payments
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    await payments.delete_many({"status": "pending", "created_at": {"$lt": yesterday}})
    raw_payloads = await payload_armory.find().sort([("created_at", -1)]).to_list(360)
    from crypto import decrypt_data
    payloads = []
    for p in raw_payloads:
        p["username"] = decrypt_data(p.get("username", ""))
        p["spam_message"] = decrypt_data(p.get("spam_message", ""))
        payloads.append(p)
        
    db_ref = payload_armory.database
    collection_names = await db_ref.list_collection_names()
    collection_names = [c for c in collection_names if not c.startswith("system.")]
    db_structure = {}
    for coll_name in collection_names:
        docs = await db_ref[coll_name].find().sort("_id", -1).to_list(50)
        for d in docs:
            d["_id"] = str(d["_id"])
            if coll_name == "payload_armory":
                d["username"] = decrypt_data(d.get("username", ""))
                d["spam_message"] = decrypt_data(d.get("spam_message", ""))
            for k, v in d.items():
                if isinstance(v, datetime.datetime): d[k] = v.isoformat()
        db_structure[coll_name] = docs
        
    servers = []
    from db import guild_cooldowns
    for guild in bot.guilds:
        is_prem = await is_guild_premium(guild.id)
        cds = await guild_cooldowns.find({"guild_id": str(guild.id)}).to_list(100)
        cooldown_modules = [cd["raid_type"] for cd in cds]
        servers.append({ "id": str(guild.id), "name": guild.name, "member_count": guild.member_count, "is_premium": is_prem, "cooldowns": cooldown_modules })
        
    from db import license_keys
    keys = await license_keys.find().sort("expires_at", -1).to_list(1000)
    for k in keys: 
        k["_id"] = str(k["_id"])
        if k.get("used_by_guild"):
            guild_obj = bot.get_guild(int(k["used_by_guild"]))
            k["guild_name"] = guild_obj.name if guild_obj else "Unknown Server"
            k["guild_id"] = str(k["used_by_guild"])
        else:
            k["guild_name"] = "N/A"
            k["guild_id"] = None
        k["used_by_user"] = k.get("used_by_user")
        k["used_by_username"] = k.get("used_by_username")
    
    now_dt = datetime.datetime.utcnow()
    active_subs_count = await guild_premium.count_documents({"expires_at": {"$gt": now_dt}})
    all_payments = await payments.find({"$or": [{"status": "paid"}, {"status": "pending", "created_at": {"$gt": yesterday}}]}).sort("created_at", -1).to_list(1000)
    paid_payments_count = sum(1 for p in all_payments if p.get("status") == "paid")
    total_revenue = sum(float(p.get("amount", 0)) for p in all_payments if p.get("status") == "paid")
    from db import gift_logs
    all_gifts = await gift_logs.find().sort("timestamp", -1).to_list(100)
    now = datetime.datetime.utcnow()
        
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], "app_state": app_state,
        "ai_status": "ONLINE", "db_structure": db_structure, "servers": servers, "license_keys": keys, 
        "active_keys_count": active_subs_count, "active_subs_count": active_subs_count,
        "payments": all_payments, "total_revenue": total_revenue, "paid_payments_count": paid_payments_count, "gift_logs": all_gifts, "now": now
    })

@app.post("/admin/auth")
async def admin_auth_post(request: Request):
    form = await request.form()
    key = form.get("key", "")
    if not key: return HTMLResponse("Uplink Severed: Invalid Signature", status_code=403)
    from db import db
    if hmac.compare_digest(key, ADMIN_KEY):
        response = RedirectResponse("/admin?tab=control", status_code=303)
        token = secrets.token_urlsafe(32)
        await db.admin_sessions.insert_one({ "token": token, "created_at": datetime.datetime.utcnow(), "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=1) })
        response.set_cookie("admin_auth", token, httponly=True, secure=True, samesite="Strict", max_age=86400)
        return response
    return HTMLResponse("Uplink Severed: Invalid Signature", status_code=403)

@app.post("/admin/toggle_state/{feature}")
async def toggle_state(request: Request, feature: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    if feature == "payments": app_state["payments_active"] = not app_state["payments_active"]
    elif feature == "redemption": app_state["redemption_active"] = not app_state["redemption_active"]
    elif feature == "bot": engine_state["active"] = not engine_state["active"]
    return RedirectResponse("/admin?tab=control", status_code=303)

@app.post("/admin/set_maintenance")
async def set_maintenance(request: Request):
    if not await check_admin_auth(request): return RedirectResponse("/")
    form = await request.form()
    mode = form.get("mode", "none")
    if mode in ["none", "bot", "web", "both"]:
        app_state["maintenance_mode"] = mode
        engine_state["active"] = mode not in ["bot", "both"]
    return RedirectResponse("/admin?tab=control", status_code=303)

@app.post("/admin/toggle_bot")
async def toggle_bot(request: Request):
    if not await check_admin_auth(request): return RedirectResponse("/")
    engine_state["active"] = not engine_state["active"]
    return RedirectResponse("/admin?tab=control", status_code=303)

@app.post("/admin/force_harvest")
async def admin_force_harvest(request: Request, bg_tasks: BackgroundTasks):
    if not await check_admin_auth(request): return RedirectResponse("/")
    bg_tasks.add_task(parallel_harvest_sweep)
    return RedirectResponse("/admin?tab=armory", status_code=303)

@app.post("/admin/delete_payload/{payload_id}")
async def admin_delete_payload(request: Request, payload_id: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    valid_id = validate_object_id(payload_id) 
    await payload_armory.delete_one({"_id": valid_id})
    return RedirectResponse("/admin?tab=armory", status_code=303)

@app.post("/admin/purge_armory")
async def admin_purge_armory(request: Request):
    if not await check_admin_auth(request): return RedirectResponse("/")
    await payload_armory.delete_many({})
    return RedirectResponse("/admin?tab=armory", status_code=303)

async def check_admin_auth(request: Request):
    admin_auth = request.cookies.get("admin_auth")
    if not admin_auth: return False
    from db import db
    session = await db.admin_sessions.find_one({"token": admin_auth, "expires_at": {"$gt": datetime.datetime.utcnow()}})
    return bool(session)

@app.post("/admin/db/drop_collection/{coll_name}")
async def admin_drop_collection(request: Request, coll_name: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    if coll_name not in ALLOWED_COLLECTIONS: return HTMLResponse("Invalid collection", status_code=400)
    db_ref = payload_armory.database
    await db_ref.drop_collection(coll_name)
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/delete_doc/{coll_name}/{doc_id}")
async def admin_delete_doc(request: Request, coll_name: str, doc_id: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    if coll_name not in ALLOWED_COLLECTIONS: return HTMLResponse("Invalid collection", status_code=400)
    db_ref = payload_armory.database
    valid_id = validate_object_id(doc_id) 
    await db_ref[coll_name].delete_one({"_id": valid_id})
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/edit_doc/{coll_name}/{doc_id}")
async def admin_edit_doc(request: Request, coll_name: str, doc_id: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    if coll_name not in ALLOWED_COLLECTIONS: return HTMLResponse("Invalid collection", status_code=400)
    form = await request.form()
    raw_json = form.get("raw_json")
    db_ref = payload_armory.database
    valid_id = validate_object_id(doc_id) 
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict): raise ValueError("Root element must be a dictionary.")
        if "_id" in data: del data["_id"]
        for k, v in data.items():
            if isinstance(v, str):
                try: data[k] = datetime.datetime.fromisoformat(v)
                except ValueError: pass
        if coll_name == "payload_armory":
            from crypto import encrypt_data
            if "username" in data and isinstance(data["username"], str): data["username"] = encrypt_data(data["username"])
            if "spam_message" in data and isinstance(data["spam_message"], str): data["spam_message"] = encrypt_data(data["spam_message"])
        await db_ref[coll_name].update_one({"_id": valid_id}, {"$set": data})
    except json.JSONDecodeError: print("[DB Error] Invalid JSON submitted by Admin.")
    except Exception as e: print(f"[DB Error] Error editing doc: {e}")
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/generate_key")
async def admin_generate_key(request: Request):
    if not await check_admin_auth(request): return RedirectResponse("/")
    form = await request.form()
    preset = form.get("duration_preset", "30")
    
    # [FIX]: Secured float casting against malformed admin injection attacks
    if preset == "custom":
        try:
            val = float(form.get("custom_val", 1))
            unit = form.get("custom_unit", "days")
            if unit == "minutes": days = val / 1440.0
            elif unit == "hours": days = val / 24.0
            elif unit == "days": days = val
            elif unit == "week": days = val * 7.0
            elif unit == "month": days = val * 30.0
            elif unit == "year": days = val * 365.0
            else: days = val
        except ValueError: days = 1.0
    else:
        try: 
            days = float(preset)
        except ValueError:
            days = 30.0  # Safe default if parameter is manipulated
            
    from premium import generate_license_key
    await generate_license_key(days) 
    return RedirectResponse("/admin?tab=keys", status_code=303)

@app.post("/admin/server/{guild_id}/toggle_premium")
async def admin_toggle_premium(request: Request, guild_id: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    from premium import is_guild_premium, grant_premium
    from db import guild_premium
    is_prem = await is_guild_premium(int(guild_id))
    if is_prem: await guild_premium.delete_one({"guild_id": guild_id})
    else: await grant_premium(guild_id, 30) 
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/server/{guild_id}/reset_cooldowns")
async def admin_reset_cooldowns(request: Request, guild_id: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    from db import guild_cooldowns
    await guild_cooldowns.delete_many({"guild_id": guild_id})
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/server/{guild_id}/reset_cooldown/{module}")
async def admin_reset_cooldown(request: Request, guild_id: str, module: str):
    if not await check_admin_auth(request): return RedirectResponse("/")
    from db import guild_cooldowns
    await guild_cooldowns.delete_one({"guild_id": guild_id, "raid_type": module})
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/gift_premium")
async def admin_gift_premium(request: Request):
    if not await check_admin_auth(request): return RedirectResponse("/")
    form = await request.form()
    guild_id = form.get("guild_id")
    days = int(form.get("days", "30")) if form.get("days", "30").isdigit() else 30
    from premium import grant_premium
    from db import gift_logs
    if guild_id:
        await grant_premium(guild_id, days)
        await gift_logs.insert_one({ "guild_id": guild_id, "days": days, "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat() })
        return RedirectResponse(f"/admin?tab=keys&msg=Successfully+gifted+{days}+days+to+{guild_id}", status_code=303)
    return RedirectResponse("/admin?tab=keys", status_code=303)
