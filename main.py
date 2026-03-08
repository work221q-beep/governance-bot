import os, asyncio, httpx, discord, datetime, time, json, urllib.parse, hmac, hashlib, secrets, re
from fastapi import FastAPI, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from bson import ObjectId
from bot import start_bot, bot, engine_state
from ai import harvest_loop, harvest_payloads, parallel_harvest_sweep
from db import init_indexes, payload_armory, db
from premium import is_guild_premium

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ADMIN_KEY = os.getenv("ADMIN_KEY")
MASTER_DISCORD_ID = os.getenv("MASTER_DISCORD_ID")

if not ADMIN_KEY or not MASTER_DISCORD_ID:
    raise RuntimeError("CRITICAL: ADMIN_KEY and MASTER_DISCORD_ID environment variables must be set.")

ALLOWED_COLLECTIONS = ["payload_armory", "guild_premium", "guild_cooldowns", "license_keys", "payments", "gift_logs", "sessions", "audit_logs", "admin_sessions"]

def validate_object_id(doc_id: str) -> ObjectId:
    if not re.match(r'^[a-fA-F0-9]{24}$', doc_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return ObjectId(doc_id)

async def get_reliable_member(guild, user_id: int):
    member = guild.get_member(user_id)
    if not member:
        try: member = await guild.fetch_member(user_id)
        except discord.NotFound: return None
    return member

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# --- 24/7 KEEP-ALIVE SYSTEM ---
@app.get("/api/health")
async def health_check():
    """Lightweight endpoint to keep the Render service awake."""
    return HTMLResponse("OK", status_code=200)

async def keep_awake_loop():
    """Background task that pings the web server internally every 10 minutes."""
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    target_url = f"{base_url}/api/health"
    while True:
        await asyncio.sleep(10 * 60)
        try:
            async with httpx.AsyncClient() as client:
                await client.get(target_url, timeout=10.0)
        except Exception:
            pass

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(harvest_loop())
    asyncio.create_task(keep_awake_loop()) # Active anti-sleep loop

@app.get("/")
async def home(request: Request):
    session_id = request.cookies.get("session_id")
    user = None
    if session_id:
        session_doc = await db.sessions.find_one({"session_id": session_id})
        if session_doc: user = session_doc["user"]
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login")
async def login(next_url: str = None):
    if next_url and not (next_url.startswith("/") and not next_url.startswith("//")): next_url = None
    state = f"login_{next_url}" if next_url else "login"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={encoded_uri}&scope=identify%20guilds&state={urllib.parse.quote(state)}"
    return RedirectResponse(url)

@app.get("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id: await db.sessions.delete_one({"session_id": session_id})
    response = RedirectResponse(url="/")
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

    if error:
        if state and "premium" in state: return RedirectResponse(url="/")
        return RedirectResponse(url="/login")
        
    if not code:
        if state and "premium" in state: return RedirectResponse(url="/")
        return RedirectResponse(url="/login")

    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data={
            "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI
        })
        access_token = token_res.json().get("access_token")
        user = (await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})).json()
        guilds = (await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})).json()

    manageable_guilds = []
    for g in guilds:
        try:
            perms_str = str(g.get("permissions", "0"))
            if len(perms_str) > 20: continue 
            if g.get("owner") or (int(perms_str) & 0x8) == 0x8:
                manageable_guilds.append(g)
        except (ValueError, TypeError): continue
            
    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png" if user.get("avatar") else None
    
    redirect_url = "/dashboard"
    if state and state.startswith("login_"):
        parts = state.split("_", 1)
        if len(parts) > 1 and parts[1]:
            parsed_url = urllib.parse.unquote(parts[1])
            if parsed_url.startswith("/") and not parsed_url.startswith("//"): redirect_url = parsed_url
    elif state and state.startswith("invite"): redirect_url = "/"
            
    response = RedirectResponse(url=redirect_url)
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    user_data = { "id": user["id"], "username": user["username"], "global_name": user.get("global_name"), "avatar": avatar_url, "guilds": manageable_guilds }
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=7)
    await db.sessions.insert_one({ "session_id": session_id, "user": user_data, "csrf_token": csrf_token, "created_at": datetime.datetime.utcnow(), "expires_at": expires_at })
    
    response.set_cookie("session_id", session_id, httponly=True, secure=True, samesite="lax", max_age=7*24*60*60)
    return response

async def get_session_user(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id: return None, None
    session_doc = await db.sessions.find_one({"session_id": session_id})
    if not session_doc: return None, None
    return session_doc["user"], session_doc.get("csrf_token")

@app.get("/dashboard")
async def dashboard(request: Request):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    for guild in session_user.get("guilds", []):
        guild["is_premium"] = await is_guild_premium(guild["id"])
        
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": session_user, "is_master": is_master})

@app.get("/server/{guild_id}")
async def redirect_to_permissions(guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions")

@app.get("/server/{guild_id}/permissions")
async def permissions_manager(request: Request, guild_id: str, tab: str = "roles", error: str = None, error_title: str = None):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
        
    guild = bot.get_guild(int(guild_id))
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    has_premium = await is_guild_premium(guild_id)
    
    user_power = "Moderator"
    is_authorized = False
    
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            try:
                perms_str = str(g.get("permissions", "0"))
                if len(perms_str) <= 20:
                    if g.get("owner"):
                        user_power = "Owner"
                        is_authorized = True
                    elif (int(perms_str) & 0x8) == 0x8:
                        user_power = "Administrator"
                        is_authorized = True
            except (ValueError, TypeError): pass
            break
            
    if not is_authorized: return RedirectResponse("/dashboard?error=You do not have permission to access this server.")
        
    roles, users, bots, channels = [], [], [], []
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if bot_in_guild else None

    if bot_in_guild and web_member:
        if not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id):
            return RedirectResponse("/dashboard?error=Your permissions in this server have changed. Access denied.")
    elif bot_in_guild and not web_member: return RedirectResponse("/dashboard?error=You are no longer in this server.")
        
    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")
    
    from db import payments, guild_premium
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    # UI Filter for rendering ledgers
    guild_payments = await payments.find({
        "guild_id": str(guild_id),
        "$or": [{"status": "paid"}, {"status": "pending", "created_at": {"$gt": yesterday}}]
    }).sort("created_at", -1).to_list(100)
    
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    premium_expires_at = prem_doc["expires_at"].isoformat() if prem_doc and "expires_at" in prem_doc else None
    
    if bot_in_guild:
        for r in reversed(guild.roles):
            current_perms = [perm[0] for perm in r.permissions if perm[1]]
            can_edit = True
            edit_reason = ""
            
            if r.managed:
                can_edit = False; edit_reason = "Managed by an integration"
            elif r >= guild.me.top_role and guild.owner_id != guild.me.id:
                can_edit = False; edit_reason = "Role is higher or equal to the bot's highest role"
            elif user_power not in ["Owner", "Administrator"]:
                if web_member and r >= web_member.top_role and guild.owner_id != web_member.id:
                    can_edit = False; edit_reason = "Role is higher or equal to your highest role"
                
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
            
        for c in sorted_channels:
            channels.append({"id": str(c.id), "name": c.name, "type": str(c.type)})

    return templates.TemplateResponse("permissions.html", {
        "request": request, "guild_id": guild_id, "roles": roles, "users": users, 
        "bots": bots, "channels": channels, "guild_name": guild_name,
        "user": session_user, "bot_in_guild": bot_in_guild,
        "has_premium": has_premium, "user_power": user_power,
        "display_name": display_name, "user_avatar": user_avatar,
        "guild_payments": guild_payments, "premium_expires_at": premium_expires_at,
        "active_tab": tab, "error": error, "error_title": error_title,
        "csrf_token": csrf_token
    })

@app.post("/server/{guild_id}/sync")
async def apply_sync_post(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    managed_perms = [
        "administrator", "manage_guild", "manage_roles", "manage_channels", "kick_members", "ban_members", 
        "send_messages", "embed_links", "attach_files", "manage_messages", "mention_everyone", "manage_webhooks", 
        "connect", "speak", "mute_members", "move_members", "manage_events", "view_audit_log"
    ]
    
    for role in guild.roles:
        if role >= guild.me.top_role and guild.owner_id != guild.me.id: continue
        if role.managed: continue
            
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
            try: await role.edit(permissions=new_perms, reason="Sylas Web Admin: Bulk Infrastructure Sync")
            except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?error=Bot lacks permission to edit role {role.name}.&error_title=Bot Permission Error", status_code=303)
            except Exception as e: return RedirectResponse(f"/server/{guild_id}/permissions?error=Failed to edit role {role.name}: {str(e)}&error_title=Error", status_code=303)

    return RedirectResponse(f"/server/{guild_id}/permissions", status_code=303)

@app.get("/server/{guild_id}/premium")
async def premium_manager(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse(f"/login?next_url={urllib.parse.quote(f'/server/{guild_id}/premium')}")
        
    guild = bot.get_guild(int(guild_id))
    bot_in_guild = True if guild else False
    guild_name = guild.name if bot_in_guild else next((g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)), "Unknown Server")

    has_premium = await is_guild_premium(int(guild_id))
    from db import guild_premium
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    premium_expires_at = prem_doc["expires_at"].isoformat() if prem_doc and "expires_at" in prem_doc else None

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
        "csrf_token": csrf_token
    })


# --- VULNERABILITY PATCHED PAYMENT FLOW ---

@app.post("/server/{guild_id}/buy_premium")
async def buy_premium(request: Request, guild_id: str):
    import uuid
    from db import payments
    
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
    guild = bot.get_guild(int(guild_id))
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if guild else None
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id): raise HTTPException(status_code=403, detail="Permission denied")
        
    plan = form_data.get("plan", "monthly")
    amount = 5.00 if plan == "weekly" else (190.00 if plan == "yearly" else 17.99)
    days = 7 if plan == "weekly" else (365 if plan == "yearly" else 30)
    order_id = f"SYLAS-{guild_id}-{uuid.uuid4().hex[:8]}"
    
    # Store initial pending layout
    await payments.insert_one({
        "internal_order_id": order_id, "guild_id": guild_id, "user_id": session_user.get("id"),
        "amount": amount, "days": days, "status": "pending",
        "created_at": datetime.datetime.utcnow()
    })

    merchant_wallet = os.getenv("MERCHANT_WALLET", "0x0000000000000000000000000000000000000000")
    base_url = os.getenv("BASE_URL", str(request.base_url).rstrip('/'))
    
    async with httpx.AsyncClient() as client:
        try:
            # FIX: Use ?id= requirement from docs for correct IPN session linking
            resp = await client.post("https://chain2pay.cloud/api/generate", json={
                "amount": float(amount),
                "currency": "USD",
                "merchant_wallet": merchant_wallet,
                "callback_url": f"{base_url}/api/webhook/chain2pay?id={order_id}",
                "customer_email": "admin@sylas.ai"
            })
            data = resp.json()
            
            if data.get("success"):
                payment_url = data.get("payment_url")
                c2p_order_id = data.get("order_id")
                ipn_token = data.get("ipn_token")
                
                # CRITICAL SECURITY FIX: Save IPN Token for webhook validation
                await payments.update_one(
                    {"internal_order_id": order_id},
                    {"$set": {"chain2pay_order_id": c2p_order_id, "ipn_token": ipn_token}}
                )

                if payment_url:
                    # FIX: Append checkout domain if relative path returned
                    if not payment_url.startswith("http"):
                        payment_url = f"https://checkout.chain2pay.cloud/{payment_url.lstrip('/')}"
                    return RedirectResponse(payment_url, status_code=303)
        except Exception as e:
            print(f"Chain2Pay API Error: {e}")
            
    return RedirectResponse(f"/server/{guild_id}/premium?error=Payment gateway unavailable. Try again later.", status_code=303)


@app.get("/api/webhook/chain2pay")
async def chain2pay_webhook(request: Request):
    """
    CRITICAL SECURITY FIX: Actively verifies transaction authenticity with the Chain2Pay API
    using the stored IPN Token, nullifying webhook spoofing vulnerabilities.
    """
    internal_id = request.query_params.get("id")
    txid_out = request.query_params.get("txid_out")
    
    from db import payments
    from premium import generate_license_key, redeem_license_key
    
    payment = await payments.find_one({"internal_order_id": internal_id})
    if not payment or payment["status"] == "paid":
        return HTMLResponse("Ignored", status_code=200)

    ipn_token = payment.get("ipn_token")
    if not ipn_token:
        return HTMLResponse("Missing Security Token", status_code=400)

    async with httpx.AsyncClient() as client:
        try:
            # Active cryptographic status check
            verify_resp = await client.get(f"https://api.chain2pay.cloud/control/payment-status.php?ipn_token={ipn_token}")
            verify_data = verify_resp.json()
            
            if verify_data.get("status") == "paid":
                await payments.update_one(
                    {"_id": payment["_id"]}, 
                    {"$set": {"status": "paid", "txid_out": txid_out}}
                )
                key = await generate_license_key(payment["days"])
                await redeem_license_key(payment["guild_id"], key)
                return HTMLResponse("OK", status_code=200)
        except Exception as e:
            print(f"Webhook Verification Failed: {e}")
            
    return HTMLResponse("Unverified Transaction", status_code=400)


@app.post("/server/{guild_id}/redeem_key")
async def redeem_key(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
    guild = bot.get_guild(int(guild_id))
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if guild else None
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id): raise HTTPException(status_code=403, detail="Permission denied")
        
    # SECURITY FIX: Brute Force Key Rate Limiting
    if not hasattr(app.state, 'redeem_rl'): app.state.redeem_rl = {}
    now = time.time()
    last_attempt = app.state.redeem_rl.get(guild_id, 0)
    if now - last_attempt < 5:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Please wait 5 seconds before trying again.&error_title=Rate Limited", status_code=303)
    app.state.redeem_rl[guild_id] = now
    
    key = form_data.get("license_key", "").strip()
    if not re.match(r'^SYLAS-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$', key): 
        return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid key format.&error_title=Failed", status_code=303)
    
    from db import license_keys
    key_doc = await license_keys.find_one({"key": key, "used": False})
    if not key_doc:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid or consumed license key.&error_title=Redemption Failed", status_code=303)

    from premium import redeem_license_key
    success = await redeem_license_key(guild_id, key)
    if success: return RedirectResponse(f"/server/{guild_id}/premium?success=true", status_code=303)
    else: return RedirectResponse(f"/server/{guild_id}/premium?error=Error redeeming license key.&error_title=Redemption Failed", status_code=303)


@app.post("/server/{guild_id}/action/{action}/{target_id}")
async def mod_action(request: Request, guild_id: str, action: str, target_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
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
        elif action == "timeout": await target.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=timeout_duration), reason=audit_log_reason)
    except discord.Forbidden: return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Unknown Bot Permission Error during execution.&error_title=Execution Failed", status_code=303)
            
    return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}", status_code=303)

@app.post("/server/{guild_id}/channel/{channel_id}/override")
async def channel_override(request: Request, guild_id: str, channel_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
    role_id = form_data.get("role_id")
    guild = bot.get_guild(int(guild_id))
    if not guild: return RedirectResponse(f"/server/{guild_id}/permissions")
    
    channel = guild.get_channel(int(channel_id))
    role = guild.get_role(int(role_id)) if role_id else guild.default_role
    
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or web_member.guild_permissions.manage_channels or guild.owner_id == web_member.id): raise HTTPException(status_code=403, detail="Permission denied")
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
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
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
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
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
    session_user, csrf_token = await get_session_user(request)
    if not session_user: return RedirectResponse("/login")
    
    form_data = await request.form()
    if not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token): raise HTTPException(status_code=403, detail="CSRF token mismatch")
        
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
async def admin_panel(request: Request, key: str = None):
    admin_auth = request.cookies.get("admin_auth")
    from db import db
    
    if key and hmac.compare_digest(key, ADMIN_KEY):
        response = RedirectResponse("/admin")
        token = secrets.token_urlsafe(32)
        await db.admin_sessions.insert_one({ "token": token, "created_at": datetime.datetime.utcnow(), "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=1) })
        response.set_cookie("admin_auth", token, httponly=True, secure=True, samesite="lax", max_age=86400)
        return response
        
    if not admin_auth: return HTMLResponse("Unauthorized", status_code=403)
    session = await db.admin_sessions.find_one({ "token": admin_auth, "expires_at": {"$gt": datetime.datetime.utcnow()} })
    if not session: return HTMLResponse("Unauthorized", status_code=403)
    
    raw_payloads = await payload_armory.find().sort([("raid_type", 1), ("created_at", -1)]).to_list(1000)
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
        docs = await db_ref[coll_name].find().sort("_id", -1).to_list(1000)
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
        
    from db import license_keys, payments
    keys = await license_keys.find().sort("expires_at", -1).to_list(1000)
    for k in keys: k["_id"] = str(k["_id"])
    active_keys_count = sum(1 for k in keys if not k.get("used", False))
        
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    all_payments = await payments.find({
        "$or": [{"status": "paid"}, {"status": "pending", "created_at": {"$gt": yesterday}}]
    }).sort("created_at", -1).to_list(1000)
    
    # Accurate UI Dashboard metric calculations
    paid_payments_count = sum(1 for p in all_payments if p.get("status") == "paid")
    total_revenue = sum(float(p.get("amount", 0)) for p in all_payments if p.get("status") == "paid")
    
    from db import gift_logs
    all_gifts = await gift_logs.find().sort("timestamp", -1).to_list(100)
        
    return templates.TemplateResponse("admin.html", {
        "request": request, "payloads": payloads, "bot_active": engine_state["active"], 
        "ai_status": "ONLINE", "db_structure": db_structure,
        "servers": servers, "license_keys": keys, "active_keys_count": active_keys_count,
        "payments": all_payments, "total_revenue": total_revenue, "paid_payments_count": paid_payments_count, "gift_logs": all_gifts
    })

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
        if "_id" in data: del data["_id"]
        if coll_name == "payload_armory":
            from crypto import encrypt_data
            if "username" in data: data["username"] = encrypt_data(data["username"])
            if "spam_message" in data: data["spam_message"] = encrypt_data(data["spam_message"])
        await db_ref[coll_name].update_one({"_id": valid_id}, {"$set": data})
    except Exception as e: print(f"Error editing doc: {e}")
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/generate_key")
async def admin_generate_key(request: Request):
    if not await check_admin_auth(request): return RedirectResponse("/")
    form = await request.form()
    days = int(form.get("days", 30))
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