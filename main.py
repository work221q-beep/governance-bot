import os, asyncio, httpx, discord, datetime, time, json, urllib.parse, hmac, secrets, re, hashlib
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.logger import logger
from bson import ObjectId
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from bot import start_bot, bot, engine_state
from ai import harvest_loop, parallel_harvest_sweep
from db import init_indexes, payload_armory, db
from premium import is_guild_premium

# === RATE LIMITER ===
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# === TEMPLATES ===
templates = Jinja2Templates(directory="templates")

# === SECURITY HEADERS MIDDLEWARE ===
@app.middleware("http")
async def security_headers(request: Request, call_next):
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
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:;"
    return response

# --- DYNAMIC GRAMMATICAL DURATION FORMATTER ---
def format_duration(days_float):
    try:
        days_float = float(days_float)
    except (ValueError, TypeError):
        return "0 Mins"
        
    def fmt(val, sing, plur):
        n = int(val) if val.is_integer() else round(val, 2)
        return f"{n} {sing if n == 1 else plur}"
        
    if days_float >= 365:
        return fmt(days_float / 365.0, "Year", "Years")
    elif days_float >= 30:
        return fmt(days_float / 30.0, "Month", "Months")
    elif days_float >= 7:
        return fmt(days_float / 7.0, "Week", "Weeks")
    elif days_float >= 1:
        return fmt(days_float, "Day", "Days")
    elif days_float >= (1/24.0):
        return fmt(days_float * 24.0, "Hour", "Hours")
    else:
        return fmt(days_float * 1440.0, "Min", "Mins")

templates.env.globals["format_duration"] = format_duration
# ----------------------------------------------

# === ENVIRONMENT VALIDATION ===
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ADMIN_KEY = os.getenv("ADMIN_KEY")
MASTER_DISCORD_ID = os.getenv("MASTER_DISCORD_ID")
BASE_URL = os.getenv("BASE_URL")

if not all([DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI, ADMIN_KEY, MASTER_DISCORD_ID, BASE_URL]):
    raise RuntimeError("CRITICAL: Missing required environment variables.")

ALLOWED_COLLECTIONS = [
    "payload_armory", "guild_premium", "guild_cooldowns", "license_keys",
    "payments", "gift_logs", "sessions", "audit_logs", "admin_sessions", "premium_gifts"
]

app_state = {
    "payments_active": True,
    "redemption_active": True,
    "maintenance_mode": "none"
}

# === UTILS ===
def validate_object_id(doc_id: str) -> ObjectId:
    if not re.match(r'^[a-fA-F0-9]{24}
👉 **Wait for Part 2/4** — coming next.  
Do **not** run this yet — it’s incomplete.

Type: **"Send Part 2"** to continue., doc_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return ObjectId(doc_id)

async def get_reliable_member(guild, user_id: int):
    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
    return member

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    return HTMLResponse("OK", status_code=200)

# === BACKGROUND TASKS ===
async def keep_awake_loop():
    target_url = f"{BASE_URL.rstrip('/')}/api/health"
    while True:
        await asyncio.sleep(10 * 60)
        try:
            async with httpx.AsyncClient() as client:
                await client.get(target_url, timeout=10.0)
        except Exception as e:
            logger.warning(f"[KeepAlive] Failed: {e}")
        try:
            now = datetime.datetime.utcnow()
            await db.sessions.delete_many({"expires_at": {"$lt": now}})
            await db.admin_sessions.delete_many({"expires_at": {"$lt": now}})
        except Exception as e:
            logger.error(f"[Cleanup Error] {e}")

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
            logger.error(f"[Reconciliation Daemon] Error: {e}")
        await asyncio.sleep(30)

async def verify_and_fulfill_payment(token: str):
    from db import payments, license_keys
    from premium import generate_license_key
    paymento_api_key = os.getenv("PAYMENTO_API_KEY")
    if not paymento_api_key:
        logger.error("[Payment] PAYMENTO_API_KEY missing.")
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            verify_resp = await client.post(
                "https://api.paymento.io/v1/payment/verify",
                headers={"Api-key": paymento_api_key, "Content-Type": "application/json"},
                json={"token": token}
            )
            verify_data = verify_resp.json()
            if verify_data.get("success") and "body" in verify_data:
                trusted_order_id = verify_data["body"].get("orderId")
                if not trusted_order_id:
                    logger.warning(f"[Payment] No orderId in verification: {token}")
                    return None
                payment = await payments.find_one({"internal_order_id": trusted_order_id})
                if not payment:
                    logger.warning(f"[Payment] No payment found for orderId: {trusted_order_id}")
                    return None
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
                            "used": False,
                            "purchased_by": str(payment.get("user_id", "")),
                            "purchased_by_username": payment.get("username", "Unknown"),
                            "internal_order_id": payment["internal_order_id"],
                            "duration_days": payment["days"],
                            "acknowledged": False,
                            "shown_count": 0,
                            "created_at": datetime.datetime.utcnow()
                        }}
                    )
                    logger.info(f"[Payment] Key generated: {key} for {trusted_order_id}")
                    return await license_keys.find_one({"key": key})
        except Exception as e:
            logger.error(f"[Payment] Verification failed for token {token}: {e}")
    return None

async def jit_payment_reconciliation(user_id: str):
    from db import payments
    try:
        pending = await payments.find({
            "user_id": user_id,
            "status": "pending",
            "paymento_token": {"$exists": True}
        }).to_list(5)
        for p in pending:
            await verify_and_fulfill_payment(p["paymento_token"])
    except Exception as e:
        logger.error(f"[JIT Reconciliation] Failed for user {user_id}: {e}")

@app.on_event("startup")
async def startup_event():
    await init_indexes()
    asyncio.create_task(start_bot())
    asyncio.create_task(harvest_loop())
    asyncio.create_task(keep_awake_loop())
    asyncio.create_task(payment_reconciliation_loop())
    logger.info("✅ All background tasks started.")

# === AUTH ROUTES ===
@app.get("/")
async def home(request: Request):
    session_id = request.cookies.get("session_id")
    user = None
    if session_id:
        session_doc = await db.sessions.find_one({"session_id": session_id})
        if session_doc:
            user = session_doc["user"]
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login")
@limiter.limit("5/minute")
async def login(request: Request, next_url: str = None):
    # Only allow safe internal paths
    if next_url and not re.match(r'^/[^?#][^ ]*
✅ **Part 2/4 complete.**

👉 Type: **"Send Part 3"** to continue., next_url):
        next_url = None
    if next_url and not next_url.startswith("/"):
        next_url = None

    oauth_state = secrets.token_urlsafe(32)
    encoded_next_url = urllib.parse.quote(next_url, safe="") if next_url else "none"
    state = f"login_{encoded_next_url}_{oauth_state}"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&response_type=code&redirect_uri={encoded_uri}&scope=identify%20guilds"
        f"&state={urllib.parse.quote(state)}"
    )
    response = RedirectResponse(url)
    response.set_cookie("oauth_state", oauth_state, httponly=True, secure=True, max_age=300, samesite="Lax")
    return response

@app.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id:
        await db.sessions.delete_one({"session_id": session_id})
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_id", path="/", secure=True, httponly=True)
    response.delete_cookie("admin_auth", path="/", secure=True, httponly=True)
    response.delete_cookie("oauth_state", path="/")
    return response

@app.get("/invite")
async def invite_bot(guild_id: str = None):
    state = f"invite_{guild_id}" if guild_id else "invite"
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&permissions=8&scope=bot&redirect_uri={encoded_uri}&response_type=code&state={state}"
    )
    if guild_id:
        url += f"&guild_id={guild_id}&disable_guild_select=true"
    return RedirectResponse(url)

@app.get("/auth/callback")
@limiter.limit("10/minute")
async def callback(request: Request, code: str = None, error: str = None, state: str = None):
    if state and state.startswith("invite"):
        if error:
            return RedirectResponse(url="/")
        parts = state.split("_")
        if len(parts) > 1 and parts[1] and parts[1].isdigit():
            guild_id = parts[1]
            return HTMLResponse(content=f"""
            <html><head><meta http-equiv='refresh' content='3;url=/server/{guild_id}/permissions' />
            <style>body {{ background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}</style>
            </head><body><h2 style='font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;'>AUTHORIZED. REDIRECTING...</h2></body></html>
            """)
        else:
            return HTMLResponse(content="""
            <html><head><meta http-equiv='refresh' content='3;url=/dashboard' />
            <style>body { background: #030305; color: white; font-family: 'Space Grotesk', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }</style>
            </head><body><h2 style='font-size: 2rem; font-weight: 900; letter-spacing: 0.1em;'>AUTHORIZED. REDIRECTING...</h2></body></html>
            """)

    if error or not code:
        return RedirectResponse(url="/login")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI
            }
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse(url="/login?error=auth_failed")

        user_res = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_res.status_code != 200:
            return RedirectResponse(url="/login?error=discord_api_failure")

        user = user_res.json()
        guilds_res = await client.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        guilds = guilds_res.json()

    manageable_guilds = []
    for g in guilds:
        try:
            perms_str = str(g.get("permissions", "0"))
            if len(perms_str) > 20:
                continue
            if g.get("owner") or (int(perms_str) & 0x8) == 0x8:
                manageable_guilds.append(g)
        except (ValueError, TypeError):
            continue

    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png" if user.get("avatar") else None

    redirect_url = "/dashboard"
    if state and state.startswith("login_"):
        parts = state.split("_", 2)
        expected_state = request.cookies.get("oauth_state")
        if len(parts) != 3 or parts[2] != expected_state:
            return RedirectResponse(url="/login?error=csrf_validation_failed")
        if parts[1] != "none":
            parsed_url = urllib.parse.unquote(parts[1])
            if re.match(r'^/[^?#][^ ]*
✅ **Part 2/4 complete.**

👉 Type: **"Send Part 3"** to continue., parsed_url) and parsed_url.startswith("/"):
                redirect_url = parsed_url

    response = RedirectResponse(url=redirect_url)
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    user_data = {
        "id": user["id"],
        "username": user["username"],
        "global_name": user.get("global_name"),
        "avatar": avatar_url,
        "guilds": manageable_guilds
    }

    await db.sessions.insert_one({
        "session_id": session_id,
        "user": user_data,
        "csrf_token": csrf_token,
        "created_at": datetime.datetime.utcnow(),
        "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    })
    await jit_payment_reconciliation(user_data["id"])
    response.set_cookie("session_id", session_id, httponly=True, secure=True, samesite="Lax", max_age=7*24*60*60)
    response.delete_cookie("oauth_state", path="/")
    return response

async def get_session_user(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None, None
    session_doc = await db.sessions.find_one({"session_id": session_id})
    if not session_doc:
        return None, None
    return session_doc["user"], session_doc.get("csrf_token")

# === API ENDPOINTS ===
@app.post("/api/keys/acknowledge")
@limiter.limit("10/minute")
async def acknowledge_key(request: Request):
    session_user, _ = await get_session_user(request)
    if not session_user:
        return HTMLResponse(status_code= 401)
    try:
        data = await request.json()
        key = data.get("key")
        if not key:
            return HTMLResponse(status_code= 400)
        from db import license_keys
        result = await license_keys.update_one(
            {"key": key, "purchased_by": str(session_user["id"])},
            {"$set": {"acknowledged": True}}
        )
        if result.modified_count == 0:
            logger.warning(f"[Ack] Unauthorized or invalid key: {key} by user {session_user['id']}")
        return HTMLResponse("OK")
    except Exception as e:
        logger.error(f"[Ack] Failed: {e}")
        return HTMLResponse(status_code= 500)

@app.post("/api/keys/mark_shown")
@limiter.limit("30/minute")
async def mark_key_shown(request: Request):
    session_user, _ = await get_session_user(request)
    if not session_user:
        return HTMLResponse(status_code= 401)
    try:
        data = await request.json()
        key = data.get("key")
        if not key:
            return HTMLResponse(status_code= 400)
        from db import license_keys
        await license_keys.update_one(
            {"key": key, "purchased_by": str(session_user["id"])},
            {"$inc": {"shown_count": 1}}
        )
        return HTMLResponse("OK")
    except Exception as e:
        logger.error(f"[MarkShown] Failed: {e}")
        return HTMLResponse(status_code= 500)

@app.post("/user/redeem_universal")
@limiter.limit("5/minute")
async def redeem_universal(request: Request):
    session_user, _ = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")
    if not app_state["redemption_active"]:
        return RedirectResponse("/dashboard?error=Key+redemption+is+temporarily+disabled+for+maintenance.")

    form_data = await request.form()
    guild_id = form_data.get("guild_id")
    key = form_data.get("license_key", "").strip()

    # Validate guild admin
    is_admin = False
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            perms_str = str(g.get("permissions", "0"))
            if g.get("owner") or (int(perms_str) & 0x8) == 0x8:
                is_admin = True
            break
    if not is_admin:
        return RedirectResponse("/dashboard?error=You+lack+admin+permissions+for+that+server.")

    guild = bot.get_guild(int(guild_id))
    if not guild:
        referer = request.headers.get("referer", "/dashboard")
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(f"{referer}{sep}bot_missing_guild={guild_id}", status_code= 303)

    from premium import redeem_license_key
    from db import license_keys

    # 🔐 Enforce ownership: only allow redeeming keys the user purchased
    key_doc = await license_keys.find_one({
        "key": key,
        "used": False,
        "purchased_by": str(session_user["id"])
    })
    if not key_doc:
        logger.warning(f"[Redeem] Invalid or unowned key: {key} by user {session_user['id']}")
        return RedirectResponse("/dashboard?error=Invalid+or+unowned+license+key.", status_code= 303)

    success = await redeem_license_key(guild_id, key)
    if success:
        await license_keys.update_one(
            {"key": key},
            {"$set": {
                "used_by_user": str(session_user.get("id")),
                "used_by_username": session_user.get("username", "Unknown"),
                "acknowledged": True,
                "used": True,
                "used_at": datetime.datetime.utcnow(),
                "used_by_guild": str(guild_id)
            }}
        )
        logger.info(f"[Redeem] Key {key} used by {session_user['id']} for guild {guild_id}")
        return RedirectResponse(f"/server/{guild_id}/premium?success=true", status_code= 303)
    else:
        return RedirectResponse("/dashboard?error=Key+is+invalid+or+already+consumed.", status_code= 303)

@app.get("/dashboard")
@limiter.limit("20/minute")
async def dashboard(request: Request):
    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")
    await jit_payment_reconciliation(session_user.get("id"))

    is_master = str(session_user.get("id")) == str(MASTER_DISCORD_ID)
    from db import payments, license_keys

    user_payments = await payments.find({"user_id": session_user.get("id")}).sort("created_at", -1).to_list(50)
    user_keys = await license_keys.find({
        "purchased_by": str(session_user.get("id")),
        "used": False
    }).sort("_id", -1).to_list(50)

    unacknowledged_key = await license_keys.find_one({
        "purchased_by": str(session_user.get("id")),
        "used": False,
        "acknowledged": False
    })

    for guild in session_user.get("guilds", []):
        guild["is_premium"] = await is_guild_premium(guild["id"])

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": session_user,
        "is_master": is_master,
        "user_payments": user_payments,
        "user_keys": user_keys,
        "unacknowledged_key": unacknowledged_key,
        "csrf_token": csrf_token
    })

@app.get("/server/{guild_id}")
async def redirect_to_permissions(guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions")

@app.get("/server/{guild_id}/permissions")
@limiter.limit("50/minute")
async def permissions_manager(request: Request, guild_id: str, tab: str = "roles", error: str = None, error_title: str = None):
    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")
    await jit_payment_reconciliation(session_user.get("id"))

    guild = bot.get_guild(int(guild_id))
    bot_in_guild = bool(guild)
    guild_name = guild.name if bot_in_guild else next(
        (g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)),
        "Unknown Server"
    )

    has_premium = await is_guild_premium(guild_id)
    user_power = "Moderator"
    is_authorized = False
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            perms_str = str(g.get("permissions", "0"))
            if g.get("owner"):
                user_power = "Owner"
                is_authorized = True
            elif (int(perms_str) & 0x8) == 0x8:
                user_power = "Administrator"
                is_authorized = True
            break
    if not is_authorized:
        return RedirectResponse("/dashboard?error=You+do+not+have+permission+to+access+this+server.")

    roles, users, bots, channels = [], [], [], []
    web_member = await get_reliable_member(guild, int(session_user.get("id"))) if bot_in_guild else None

    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")

    from db import payments, guild_premium, license_keys
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours= 24)
    guild_payments = await payments.find({
        "guild_id": str(guild_id),
        "$or": [
            {"status": "paid"},
            {"status": "pending", "created_at": {"$gt": yesterday}}
        ]
    }).sort("created_at", -1).to_list(100)

    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    premium_expires_at = (prem_doc["expires_at"].isoformat() + "Z") if prem_doc and "expires_at" in prem_doc else None

    unacknowledged_key = await license_keys.find_one({
        "purchased_by": str(session_user.get("id")),
        "used": False,
        "acknowledged": False
    })

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
                "id": str(r.id),
                "name": r.name,
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "is_everyone": r.name == "@everyone",
                "is_bot": r.managed,
                "current": current_perms,
                "can_edit": can_edit,
                "edit_reason": edit_reason
            })

        for m in guild.members:
            avatar = str(m.display_avatar.url) if m.display_avatar else None
            member_data = {
                "id": str(m.id),
                "name": m.name,
                "display_name": m.display_name,
                "avatar": avatar,
                "top_role": m.top_role.name if m.top_role else "None"
            }
            if m.bot:
                bots.append(member_data)
            else:
                users.append(member_data)

        sorted_channels = []
        for category, channels_in_cat in guild.by_category():
            if category:
                sorted_channels.append(category)
            sorted_channels.extend(sorted(channels_in_cat, key=lambda c: c.position))

        for c in sorted_channels:
            channels.append({"id": str(c.id), "name": c.name, "type": str(c.type)})

    latest_key = None
    if request.query_params.get('success') == "true":
        latest_key_doc = await license_keys.find_one(
            {"used_by_guild": str(guild_id), "used": True},
            sort=[("expires_at", -1)]
        )
        if latest_key_doc:
            latest_key = {"duration_days": latest_key_doc.get("duration_days", 0)}

    return templates.TemplateResponse("permissions.html", {
        "request": request,
        "guild_id": guild_id,
        "roles": roles,
        "users": users,
        "bots": bots,
        "channels": channels,
        "guild_name": guild_name,
        "user": session_user,
        "bot_in_guild": bot_in_guild,
        "has_premium": has_premium,
        "user_power": user_power,
        "display_name": display_name,
        "user_avatar": user_avatar,
        "guild_payments": guild_payments,
        "premium_expires_at": premium_expires_at,
        "active_tab": tab,
        "error": error,
        "error_title": error_title,
        "csrf_token": csrf_token,
        "latest_key": latest_key,
        "unacknowledged_key": unacknowledged_key
    })

@app.get("/server/{guild_id}/sync")
async def sync_manager_get(request: Request, guild_id: str):
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code= 303)

@app.post("/server/{guild_id}/sync")
@limiter.limit("5/minute")
async def apply_sync_post(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")
    
    form_data = await request.form()
    
    # ✅ Proper CSRF validation
    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")
    
    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id):
        raise HTTPException(status_code= 403, detail="Permission denied")
    
    managed_perms = [
        "administrator", "manage_guild", "manage_roles", "manage_channels", "kick_members", "ban_members",
        "send_messages", "embed_links", "attach_files", "manage_messages", "mention_everyone",
        "manage_webhooks", "connect", "speak", "mute_members", "move_members", "manage_events", "view_audit_log"
    ]
    
    for role in guild.roles:
        if role >= guild.me.top_role and guild.owner_id != guild.me.id:
            continue
        if role.managed:
            continue
        if web_member and guild.owner_id != web_member.id and role >= web_member.top_role:
            continue
        
        perms_list = form_data.getlist(f"perms_{role.id}")
        current_kwargs = {}
        for prop in dir(role.permissions):
            if not prop.startswith('_') and prop != 'value' and isinstance(getattr(type(role.permissions), prop, None), property):
                try:
                    current_kwargs[prop] = getattr(role.permissions, prop)
                except:
                    pass
        
        # Prevent @everyone from getting Administrator
        for p in managed_perms:
            if role.name == "@everyone" and p == "administrator":
                current_kwargs[p] = False
            else:
                current_kwargs[p] = p in perms_list
        
        try:
            new_perms = discord.Permissions(**current_kwargs)
        except Exception:
            new_perms = role.permissions
        
        if role.permissions.value != new_perms.value:
            try:
                await role.edit(permissions=new_perms, reason="Sylas Web Admin: Bulk Infrastructure Sync")
                await asyncio.sleep(0.3)
            except discord.Forbidden:
                error_msg = urllib.parse.quote(f'Bot lacks permission to edit role {role.name}.')
                return RedirectResponse(f"/server/{guild_id}/permissions?error={error_msg}&error_title=Bot+Permission+Error", status_code= 303)
            except Exception as e:
                error_msg = urllib.parse.quote(f'Failed to edit role {role.name}: {str(e)[:100]}')
                return RedirectResponse(f"/server/{guild_id}/permissions?error={error_msg}&error_title=Error", status_code= 303)
    
    return RedirectResponse(f"/server/{guild_id}/permissions", status_code= 303)

@app.get("/server/{guild_id}/premium")
@limiter.limit("20/minute")
async def premium_manager(request: Request, guild_id: str, success: str = None, error: str = None):
    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse(f"/login?next_url={urllib.parse.quote(f'/server/{guild_id}/premium')}")
    
    await jit_payment_reconciliation(session_user.get("id"))
    
    guild = bot.get_guild(int(guild_id))
    bot_in_guild = bool(guild)
    guild_name = guild.name if bot_in_guild else next(
        (g["name"] for g in session_user.get("guilds", []) if str(g["id"]) == str(guild_id)),
        "Unknown Server"
    )

    has_premium = await is_guild_premium(int(guild_id))
    from db import guild_premium, license_keys
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    premium_expires_at = (prem_doc["expires_at"].isoformat() + "Z") if prem_doc and "expires_at" in prem_doc else None

    unacknowledged_key = await license_keys.find_one({
        "purchased_by": str(session_user.get("id")),
        "used": False,
        "acknowledged": False
    })

    latest_key = None
    if success == "true":
        latest_key_doc = await license_keys.find_one(
            {"used_by_guild": str(guild_id), "used": True},
            sort=[("expires_at", -1)]
        )
        if latest_key_doc:
            latest_key = {"duration_days": latest_key_doc.get("duration_days", 0)}

    payment_return = request.query_params.get("payment_return")
    token = request.query_params.get("token")
    status = request.query_params.get("status")
    payment_state = "none"
    generated_key = None

    if payment_return == "true":
        if status == "5":
            payment_state = "canceled"
        elif token:
            key_doc = await verify_and_fulfill_payment(token)
            if key_doc:
                payment_state = "paid"
                generated_key = {"key": key_doc["key"], "duration_days": key_doc.get("duration_days", 0)}
            else:
                payment_state = "pending"

    user_power = "Moderator"
    for g in session_user.get("guilds", []):
        if str(g["id"]) == str(guild_id):
            if g.get("owner"):
                user_power = "Owner"
            elif (int(g.get("permissions", 0)) & 0x8) == 0x8:
                user_power = "Administrator"
            break

        web_member = await get_reliable_member(guild, int(session_user.get("id"))) if bot_in_guild else None
    display_name = web_member.display_name if web_member else (session_user.get("global_name") or session_user.get("username"))
    user_avatar = str(web_member.display_avatar.url) if web_member and web_member.display_avatar else session_user.get("avatar")

    return templates.TemplateResponse("premium.html", {
        "request": request,
        "guild_id": guild_id,
        "guild_name": guild_name,
        "user": session_user,
        "has_premium": has_premium,
        "premium_expires_at": premium_expires_at,
        "user_power": user_power,
        "display_name": display_name,
        "user_avatar": user_avatar,
        "csrf_token": csrf_token,
        "latest_key": latest_key,
        "success": success,
        "payment_state": payment_state,
        "generated_key": generated_key,
        "unacknowledged_key": unacknowledged_key
    })

@app.post("/server/{guild_id}/buy_premium")
@limiter.limit("10/hour")
async def buy_premium(request: Request, guild_id: str):
    if not app_state["payments_active"]:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Payment+gateway+is+currently+undergoing+maintenance.", status_code= 303)

    import uuid
    from db import payments

    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id):
        raise HTTPException(status_code= 403, detail="Permission denied")

    plan = form_data.get("plan", "monthly")
    if plan not in ["weekly", "monthly", "yearly"]:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid+plan+selected.", status_code= 303)

    amount = 5.00 if plan == "weekly" else (190.00 if plan == "yearly" else 17.99)
    days = 7 if plan == "weekly" else (365 if plan == "yearly" else 30)

    order_id = f"SYLAS-{guild_id}-{uuid.uuid4().hex[:8]}"
    created_at = datetime.datetime.utcnow()

    await payments.insert_one({
        "internal_order_id": order_id,
        "guild_id": guild_id,
        "user_id": session_user.get("id"),
        "username": session_user.get("username", "Unknown"),
        "amount": amount,
        "days": days,
        "status": "pending",
        "created_at": created_at
    })

    base_url = BASE_URL.rstrip('/')
    paymento_api_key = os.getenv("PAYMENTO_API_KEY")
    if not paymento_api_key:
        logger.error("[Payment] PAYMENTO_API_KEY missing.")
        return RedirectResponse(f"/server/{guild_id}/premium?error=Payment+gateway+unavailable.", status_code= 303)

    return_url = f"{base_url}/server/{guild_id}/premium?payment_return=true"
    paymento_endpoint = "https://api.paymento.io/v1/payment/request"

    async with httpx.AsyncClient(timeout= 15.0) as client:
        try:
            resp = await client.post(
                paymento_endpoint,
                headers={
                    "Api-key": paymento_api_key,
                    "Content-Type": "application/json",
                    "Accept": "text/plain"
                },
                json={
                    "fiatAmount": str(amount),
                    "fiatCurrency": "USD",
                    "ReturnUrl": return_url,
                    "orderId": order_id,
                    "Speed": 1,
                    "EmailAddress": "admin@sylas.ai"
                }
            )

            if resp.status_code != 200:
                logger.error(f"[Payment] Paymento request failed: {resp.status_code} {resp.text}")
                return RedirectResponse(f"/server/{guild_id}/premium?error=Payment+service+unavailable.", status_code= 303)

            data = resp.json()
            if data.get("success"):
                token = data.get("body")
                payment_url = f"https://app.paymento.io/gateway?token={token}"
                await payments.update_one(
                    {"internal_order_id": order_id},
                    {"$set": {"paymento_token": token}}
                )
                return RedirectResponse(payment_url, status_code= 303)
            else:
                logger.warning(f"[Payment] Paymento rejected request: {data}")
                return RedirectResponse(f"/server/{guild_id}/premium?error=Payment+could+not+be+initiated.", status_code= 303)

        except Exception as e:
            logger.error(f"[Payment] Exception during Paymento request: {e}")
            return RedirectResponse(f"/server/{guild_id}/premium?error=Payment+service+timeout.", status_code= 303)

async def process_payment_bg(token: str):
    try:
        await verify_and_fulfill_payment(token)
    except Exception as e:
        logger.error(f"[Background] Payment processing failed for token {token}: {e}")

@app.post("/api/webhook/paymento")
async def paymento_webhook(request: Request, bg_tasks: BackgroundTasks):
    raw_body = await request.body()
    signature = (
        request.headers.get("hmac_sha256_signature") or
        request.headers.get("x-hmac-sha256-signature") or
        request.headers.get("hmac-sha256-signature")
    )
    secret_key = os.getenv("PAYMENTO_SECRET_KEY", "")

    if not secret_key or not signature:
        logger.warning("[Webhook] Missing HMAC signature or secret key.")
        return HTMLResponse("Missing Authentication", status_code= 403)

    calculated_signature = hmac.new(
        secret_key.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest().upper()

    if not hmac.compare_digest(calculated_signature, signature.upper()):
        logger.warning("[Webhook] Invalid HMAC signature.")
        return HTMLResponse("Invalid HMAC Signature", status_code= 403)

    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError:
        logger.warning("[Webhook] Invalid JSON payload.")
        return HTMLResponse("Invalid JSON", status_code= 400)

    token = payload.get("Token") or payload.get("token")
    order_status = str(payload.get("OrderStatus") or payload.get("orderStatus"))

    if order_status == "7" and token:
        bg_tasks.add_task(process_payment_bg, token)
        logger.info(f"[Webhook] Accepted payment confirmation for token: {token}")
        return HTMLResponse("Accepted for background processing", status_code= 200)

    logger.info(f"[Webhook] Ignored status {order_status} for token {token}")
    return HTMLResponse(f"Ignored Status: {order_status}", status_code= 200)

@app.post("/server/{guild_id}/redeem_key")
@limiter.limit("5/minute")
async def redeem_key(request: Request, guild_id: str):
    if not app_state["redemption_active"]:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Key+redemption+is+currently+disabled+for+maintenance.", status_code= 303)

    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or guild.owner_id == web_member.id):
        raise HTTPException(status_code= 403, detail="Permission denied")

    if not hasattr(app.state, 'redeem_rl'):
        app.state.redeem_rl = {}
    now = time.time()
    user_id = session_user.get("id")
    if len(app.state.redeem_rl) > 1000:
        app.state.redeem_rl = {k: v for k, v in app.state.redeem_rl.items() if now - v < 300}
    if now - app.state.redeem_rl.get(user_id, 0) < 5:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Please+wait+ 5 +seconds+before+trying+again.&error_title=Rate+Limited", status_code= 303)
    app.state.redeem_rl[user_id] = now

    key = form_data.get("license_key", "").strip()

    if not re.match(r'^SYLAS-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}$', key):
        return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid+key+format.&error_title=Failed", status_code= 303)

    from db import license_keys
    from premium import redeem_license_key

    key_doc = await license_keys.find_one({
        "key": key,
        "used": False,
        "purchased_by": str(session_user["id"])
    })
    if not key_doc:
        logger.warning(f"[Redeem] Attempt to redeem unowned or invalid key: {key} by {user_id}")
        return RedirectResponse(f"/server/{guild_id}/premium?error=Invalid+or+unowned+license+key.&error_title=Redemption+Failed", status_code= 303)

    success = await redeem_license_key(guild_id, key)
    if success:
        await license_keys.update_one(
            {"key": key},
            {"$set": {
                "used": True,
                "used_by_user": str(user_id),
                "used_by_username": session_user.get("username", "Unknown"),
                "acknowledged": True,
                "used_at": datetime.datetime.utcnow(),
                "used_by_guild": str(guild_id)
            }}
        )
        logger.info(f"[Redeem] Key {key} successfully applied to guild {guild_id} by {user_id}")
        return RedirectResponse(f"/server/{guild_id}/premium?success=true", status_code= 303)
    else:
        return RedirectResponse(f"/server/{guild_id}/premium?error=Error+applying+license+key.&error_title=Redemption+Failed", status_code= 303)

@app.post("/server/{guild_id}/action/{action}/{target_id}")
@limiter.limit("20/minute")
async def mod_action(request: Request, guild_id: str, action: str, target_id: str):
    if not target_id.isdigit():
        return RedirectResponse(f"/server/{guild_id}/permissions?error=Invalid+Target+ID", status_code= 303)

    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    custom_reason = form_data.get("reason", "No reason provided.")
    include_name = form_data.get("include_name") == "on"
    timeout_duration = int(form_data.get("duration", 10))
    admin_name = session_user.get('username')

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    target = await get_reliable_member(guild, int(target_id))
    if not target:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    has_perm = False
    if action == "ban" and web_member.guild_permissions.ban_members:
        has_perm = True
    elif action == "kick" and web_member.guild_permissions.kick_members:
        has_perm = True
    elif action == "timeout" and web_member.guild_permissions.moderate_members:
        has_perm = True
    elif web_member.guild_permissions.administrator or guild.owner_id == web_member.id:
        has_perm = True
    if not has_perm:
        return RedirectResponse(f"/server/{guild_id}/permissions?error=You+lack+the+required+permissions+to+perform+this+action.&error_title=Access+Denied", status_code= 303)

    tab = "bots" if target.bot else "users"

    if web_member and guild.owner_id != web_member.id and web_member.top_role <= target.top_role:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=You+cannot+{action}+a+user+with+an+equal+or+higher+role.&error_title=Admin+Access+Denied", status_code= 303)

    if target.guild_permissions.administrator and guild.owner_id != web_member.id:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Administrators+are+immune+to+web+dashboard+moderation.&error_title=Protection+Matrix", status_code= 303)

    bot_has_perm = False
    if action == "ban" and guild.me.guild_permissions.ban_members:
        bot_has_perm = True
    elif action == "kick" and guild.me.guild_permissions.kick_members:
        bot_has_perm = True
    elif action == "timeout" and guild.me.guild_permissions.moderate_members:
        bot_has_perm = True
    elif guild.me.guild_permissions.administrator:
        bot_has_perm = True
    if not bot_has_perm:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Sylas+lacks+permissions+to+perform+this+action.+Check+bot+settings.&error_title=Bot+Permission+Error", status_code= 303)

    if guild.owner_id == target.id or guild.me.top_role <= target.top_role:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Sylas+cannot+{action}+{target.name}.+The+bot's+role+must+be+higher+than+the+target's+role.&error_title=Bot+Hierarchy+Error", status_code= 303)

    if action == "timeout" and target.guild_permissions.administrator:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Discord+API+natively+restricts+timeouts+on+Administrators.&error_title=API+Restriction", status_code= 303)

    audit_log_reason = f"Sylas Web Admin ({admin_name}): {custom_reason}"
    dm_message = f"You have been **{action}** in **{guild.name}**.\n**Reason:** {custom_reason}"
    if include_name:
        dm_message += f"\n*Action triggered by Web Admin: {admin_name}*"

    sent_dm = None
    if action in ["kick", "ban"] and not target.bot:
        try:
            sent_dm = await target.send(dm_message)
        except discord.Forbidden:
            pass

    try:
        if action == "kick":
            await target.kick(reason=audit_log_reason)
        elif action == "ban":
            await target.ban(reason=audit_log_reason)
        elif action == "timeout":
            if timeout_duration > 40320 or timeout_duration < 1:
                raise ValueError("Duration exceeds 28-day API bounds")
            until = discord.utils.utcnow() + datetime.timedelta(minutes=timeout_duration)
            await target.timeout(until, reason=audit_log_reason)
            if not target.bot:
                try:
                    await target.send(dm_message)
                except discord.Forbidden:
                    pass
    except (discord.Forbidden, discord.HTTPException, ValueError) as e:
        if sent_dm and action in ["kick", "ban"]:
            try:
                await sent_dm.delete()
            except:
                pass
        error_safe = urllib.parse.quote(str(e)[:150])
        return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}&error=Execution+Failed:+{error_safe}&error_title=Execution+Failed", status_code= 303)

    from db import db
    await db.audit_logs.insert_one({
        "action": action,
        "guild_id": guild_id,
        "target_id": target_id,
        "admin_id": session_user.get("id"),
        "reason": custom_reason,
        "timestamp": datetime.datetime.utcnow()
    })

    return RedirectResponse(f"/server/{guild_id}/permissions?tab={tab}", status_code= 303)

@app.post("/server/{guild_id}/channel/{channel_id}/override")
@limiter.limit("30/minute")
async def channel_override(request: Request, guild_id: str, channel_id: str):
    if not channel_id.isdigit():
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+Channel+ID", status_code= 303)

    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    role_id = form_data.get("role_id")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    channel = guild.get_channel(int(channel_id))
    role = guild.get_role(int(role_id)) if role_id and role_id.isdigit() else guild.default_role

        web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or 
                             (web_member.guild_permissions.manage_roles and web_member.guild_permissions.manage_channels) or 
                             guild.owner_id == web_member.id):
        raise HTTPException(status_code= 403, detail="Permission denied. Missing Manage Roles or Channels.")

    if web_member and guild.owner_id != web_member.id and web_member.top_role <= role:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You+cannot+edit+channel+permissions+for+a+role+equal+to+or+higher+than+your+own.&error_title=Hierarchy+Error", status_code= 303)

    if not channel:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel+not+found.&error_title=Not+Found", status_code= 303)

    overwrite = channel.overwrites_for(role)
    extended_perms = [
        "view_channel", "send_messages", "embed_links", "attach_files", "manage_messages",
        "read_message_history", "mention_everyone", "use_external_emojis", "add_reactions",
        "connect", "speak", "mute_members", "deafen_members", "move_members", "use_voice_activation",
        "request_to_speak", "manage_events", "send_messages_in_threads", "create_public_threads",
        "create_private_threads", "manage_threads"
    ]

    for perm in extended_perms:
        val = form_data.get(perm)
        if val == "allow":
            setattr(overwrite, perm, True)
        elif val == "deny":
            setattr(overwrite, perm, False)
        elif val == "inherit":
            setattr(overwrite, perm, None)

    try:
        await channel.set_permissions(role, overwrite=overwrite, reason="Sylas Channel Override Matrix Sync")
    except discord.Forbidden:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas+lacks+permissions+to+manage+this+channel.&error_title=Channel+Access+Denied", status_code= 303)

    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code= 303)

@app.post("/server/{guild_id}/channel/create")
@limiter.limit("10/hour")
async def create_channel(request: Request, guild_id: str):
    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    channel_name = form_data.get("channel_name", "").strip().lower()
    channel_type = form_data.get("channel_type", "text")

    if not channel_name or len(channel_name) < 2 or len(channel_name) > 100:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel+name+must+be+between+ 2 +and+ 100 +characters.&error_title=Invalid+Name", status_code= 303)

    if not re.match(r'^[a-z0-9-]+$', channel_name):
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel+names+can+only+contain+lowercase+letters,+numbers,+and+dashes.&error_title=Invalid+Name", status_code= 303)

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or web_member.guild_permissions.manage_channels or guild.owner_id == web_member.id):
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You+do+not+have+permission+to+manage+channels.&error_title=Access+Denied", status_code= 303)

    try:
        if channel_type == "text":
            await guild.create_text_channel(name=channel_name, reason="Sylas Web Admin: Channel Created")
        elif channel_type == "voice":
            await guild.create_voice_channel(name=channel_name, reason="Sylas Web Admin: Channel Created")
        elif channel_type == "category":
            await guild.create_category(name=channel_name, reason="Sylas Web Admin: Category Created")
        else:
            return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+channel+type.&error_title=Error", status_code= 303)
    except discord.Forbidden:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas+lacks+permissions+to+create+channels.&error_title=Permission+Denied", status_code= 303)

    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code= 303)

@app.post("/server/{guild_id}/channel/{channel_id}/delete")
@limiter.limit("10/hour")
async def delete_channel(request: Request, guild_id: str, channel_id: str):
    if not channel_id.isdigit():
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+Channel+ID", status_code= 303)

    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or web_member.guild_permissions.manage_channels or guild.owner_id == web_member.id):
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You+do+not+have+permission+to+manage+channels.&error_title=Access+Denied", status_code= 303)

    channel = guild.get_channel(int(channel_id))
    if not channel:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code= 303)

    try:
        await channel.delete(reason="Sylas Web Admin: Channel Deleted")
    except discord.Forbidden:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas+lacks+permissions+to+delete+this+channel.&error_title=Permission+Denied", status_code= 303)

    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code= 303)

@app.post("/server/{guild_id}/channel/{channel_id}/rename")
@limiter.limit("10/hour")
async def rename_channel(request: Request, guild_id: str, channel_id: str):
    if not channel_id.isdigit():
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Invalid+Channel+ID", status_code= 303)

    session_user, csrf_token = await get_session_user(request)
    if not session_user:
        return RedirectResponse("/login")

    form_data = await request.form()

    if not csrf_token or not hmac.compare_digest(form_data.get("csrf_token", ""), csrf_token):
        raise HTTPException(status_code= 403, detail="CSRF token mismatch")

    new_name = form_data.get("new_name", "").strip().lower()
    if not new_name or len(new_name) < 2 or len(new_name) > 100:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel+name+must+be+between+ 2 +and+ 100 +characters.&error_title=Invalid+Name", status_code= 303)

    if not re.match(r'^[a-z0-9-]+$', new_name):
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Channel+names+can+only+contain+lowercase+letters,+numbers,+and+dashes.&error_title=Invalid+Name", status_code= 303)

    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse(f"/server/{guild_id}/permissions")

    web_member = await get_reliable_member(guild, int(session_user.get("id")))
    if not web_member or not (web_member.guild_permissions.administrator or web_member.guild_permissions.manage_channels or guild.owner_id == web_member.id):
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=You+do+not+have+permission+to+manage+channels.&error_title=Access+Denied", status_code= 303)

    channel = guild.get_channel(int(channel_id))
    if not channel:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code= 303)

    try:
        await channel.edit(name=new_name, reason="Sylas Web Admin: Channel Renamed")
    except discord.Forbidden:
        return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels&error=Sylas+lacks+permissions+to+rename+this+channel.&error_title=Permission+Denied", status_code= 303)

    return RedirectResponse(f"/server/{guild_id}/permissions?tab=channels", status_code= 303)

@app.get("/admin")
async def admin_panel(request: Request):
    key_param = request.query_params.get("key")
    if key_param and hmac.compare_digest(key_param, ADMIN_KEY):
        response = RedirectResponse("/admin", status_code= 303)
        token = secrets.token_urlsafe(32)
        await db.admin_sessions.insert_one({
            "token": token,
            "created_at": datetime.datetime.utcnow(),
            "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days= 1)
        })
        response.set_cookie("admin_auth", token, httponly=True, secure=True, samesite="Strict", max_age= 86400)
        return response

    admin_auth = request.cookies.get("admin_auth")
    session = None
    if admin_auth:
        session = await db.admin_sessions.find_one({
            "token": admin_auth,
            "expires_at": {"$gt": datetime.datetime.utcnow()}
        })

    if not session:
        response = HTMLResponse(
            "<html><body style='background:#030305;color:#f00;font-family:monospace;display:flex;justify-content:center;align-items:center;height:100vh;flex-direction:column;'>"
            "<h2 style='margin-bottom:20px;letter-spacing:0.2em;'>MASTER UPLINK</h2>"
            "<form method='post' action='/admin/auth'><input type='password' name='key' placeholder='Enter Authorization Key' style='background:#111;border:1px solid #f00;color:#f00;padding:12px;font-size:16px;'><button style='background:#f00;color:#000;padding:12px 20px;border:none;cursor:pointer;font-weight:bold;margin-left:10px;'>Initialize</button></form>"
            "</body></html>", status_code= 401
        )
        if admin_auth:
            response.delete_cookie("admin_auth", path="/")
        return response

    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours= 24)
    await db.payments.delete_many({"status": "pending", "created_at": {"$lt": yesterday}})

    raw_payloads = await payload_armory.find().sort([("created_at", -1)]).to_list(360)
    from crypto import decrypt_data
    payloads = []
    for p in raw_payloads:
        p["username"] = decrypt_data(p.get("username", ""))
        p["spam_message"] = decrypt_data(p.get("spam_message", ""))
        p["_id"] = str(p["_id"])
        p["created_at"] = p["created_at"].isoformat()
        payloads.append(p)

    db_ref = payload_armory.database
    collection_names = await db_ref.list_collection_names()
    collection_names = [c for c in collection_names if not c.startswith("system.")]
    db_structure = {}
    for coll_name in collection_names:
        docs = await db_ref[coll_name].find().sort("_id", -1).to_list(50)
        coll_docs = []
        for d in docs:
            d["_id"] = str(d["_id"])
            if coll_name == "payload_armory":
                d["username"] = decrypt_data(d.get("username", ""))
                d["spam_message"] = decrypt_data(d.get("spam_message", ""))
            for k, v in d.items():
                if isinstance(v, datetime.datetime):
                    d[k] = v.isoformat()
            coll_docs.append(d)
        db_structure[coll_name] = coll_docs

    servers = []
    from db import guild_cooldowns
    for guild in bot.guilds:
        is_prem = await is_guild_premium(guild.id)
        cds = await guild_cooldowns.find({"guild_id": str(guild.id)}).to_list(100)
        cooldown_modules = [cd["raid_type"] for cd in cds]
        servers.append({
            "id": str(guild.id),
            "name": guild.name,
            "member_count": guild.member_count,
            "is_premium": is_prem,
            "cooldowns": cooldown_modules
        })

    from db import license_keys
    keys = await license_keys.find().sort("expires_at", -1).to_list(1000)
    key_list = []
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
        if isinstance(k.get("created_at"), datetime.datetime):
            k["created_at"] = k["created_at"].isoformat()
        if isinstance(k.get("expires_at"), datetime.datetime):
            k["expires_at"] = k["expires_at"].isoformat()
        key_list.append(k)

    now_dt = datetime.datetime.utcnow()
    active_subs_count = await db.guild_premium.count_documents({"expires_at": {"$gt": now_dt}})
    all_payments = await db.payments.find({
        "$or": [
            {"status": "paid"},
            {"status": "pending", "created_at": {"$gt": yesterday}}
        ]
    }).sort("created_at", -1).to_list(1000)

    paid_payments_count = sum(1 for p in all_payments if p.get("status") == "paid")
    total_revenue = sum(float(p.get("amount", 0)) for p in all_payments if p.get("status") == "paid")

    all_gifts = await db.gift_logs.find().sort("timestamp", -1).to_list(100)
    now = datetime.datetime.utcnow()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "payloads": payloads,
        "bot_active": engine_state["active"],
        "app_state": app_state,
        "ai_status": "ONLINE",
        "db_structure": db_structure,
        "servers": servers,
        "license_keys": key_list,
        "active_keys_count": active_subs_count,
        "active_subs_count": active_subs_count,
        "payments": all_payments,
        "total_revenue": total_revenue,
        "paid_payments_count": paid_payments_count,
        "gift_logs": all_gifts,
        "now": now
    })

@app.post("/admin/auth")
async def admin_auth_post(request: Request):
    form = await request.form()
    key = form.get("key", "")
    if not key:
        return HTMLResponse("Uplink Severed: Invalid Signature", status_code= 403)
    if hmac.compare_digest(key, ADMIN_KEY):
        response = RedirectResponse("/admin?tab=control", status_code= 303)
        token = secrets.token_urlsafe(32)
        await db.admin_sessions.insert_one({
            "token": token,
            "created_at": datetime.datetime.utcnow(),
            "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days= 1)
        })
        response.set_cookie("admin_auth", token, httponly=True, secure=True, samesite="Strict", max_age= 86400)
        return response
    return HTMLResponse("Uplink Severed: Invalid Signature", status_code= 403)

@app.post("/admin/toggle_state/{feature}")
async def toggle_state(request: Request, feature: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    if feature == "payments":
        app_state["payments_active"] = not app_state["payments_active"]
    elif feature == "redemption":
        app_state["redemption_active"] = not app_state["redemption_active"]
    elif feature == "bot":
        engine_state["active"] = not engine_state["active"]
    return RedirectResponse("/admin?tab=control", status_code= 303)

@app.post("/admin/set_maintenance")
async def set_maintenance(request: Request):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    form = await request.form()
    mode = form.get("mode", "none")
    if mode in ["none", "bot", "web", "both"]:
        app_state["maintenance_mode"] = mode
        engine_state["active"] = mode not in ["bot", "both"]
    return RedirectResponse("/admin?tab=control", status_code= 303)

@app.post("/admin/toggle_bot")
async def toggle_bot(request: Request):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    engine_state["active"] = not engine_state["active"]
    return RedirectResponse("/admin?tab=control", status_code= 303)

@app.post("/admin/force_harvest")
async def admin_force_harvest(request: Request, bg_tasks: BackgroundTasks):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    bg_tasks.add_task(parallel_harvest_sweep)
    return RedirectResponse("/admin?tab=armory", status_code=303)

@app.post("/admin/delete_payload/{payload_id}")
async def admin_delete_payload(request: Request, payload_id: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    valid_id = validate_object_id(payload_id)
    result = await payload_armory.delete_one({"_id": valid_id})
    if result.deleted_count == 0:
        logger.warning(f"[Admin] Attempted to delete non-existent payload: {payload_id}")
    return RedirectResponse("/admin?tab=armory", status_code=303)

@app.post("/admin/purge_armory")
async def admin_purge_armory(request: Request):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    await payload_armory.delete_many({})
    logger.info(f"[Admin] Payload armory purged by {request.client.host}")
    return RedirectResponse("/admin?tab=armory", status_code=303)

async def check_admin_auth(request: Request):
    admin_auth = request.cookies.get("admin_auth")
    if not admin_auth:
        return False
    session = await db.admin_sessions.find_one({
        "token": admin_auth,
        "expires_at": {"$gt": datetime.datetime.utcnow()}
    })
    return session is not None

@app.post("/admin/db/drop_collection/{coll_name}")
async def admin_drop_collection(request: Request, coll_name: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    if coll_name not in ALLOWED_COLLECTIONS:
        logger.warning(f"[Admin] Attempted to drop disallowed collection: {coll_name}")
        return HTMLResponse("Invalid collection", status_code=400)
    db_ref = payload_armory.database
    await db_ref.drop_collection(coll_name)
    logger.warning(f"[Admin] Collection dropped: {coll_name}")
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/delete_doc/{coll_name}/{doc_id}")
async def admin_delete_doc(request: Request, coll_name: str, doc_id: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    if coll_name not in ALLOWED_COLLECTIONS:
        return HTMLResponse("Invalid collection", status_code=400)
    db_ref = payload_armory.database
    valid_id = validate_object_id(doc_id)
    result = await db_ref[coll_name].delete_one({"_id": valid_id})
    if result.deleted_count == 0:
        logger.warning(f"[Admin] Attempted to delete non-existent doc: {coll_name}/{doc_id}")
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/db/edit_doc/{coll_name}/{doc_id}")
async def admin_edit_doc(request: Request, coll_name: str, doc_id: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    if coll_name not in ALLOWED_COLLECTIONS:
        return HTMLResponse("Invalid collection", status_code=400)
    form = await request.form()
    raw_json = form.get("raw_json")
    db_ref = payload_armory.database
    valid_id = validate_object_id(doc_id)
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise ValueError("Root element must be a dictionary.")
        if "_id" in data:
            del data["_id"]
        for k, v in data.items():
            if isinstance(v, str):
                try:
                    data[k] = datetime.datetime.fromisoformat(v)
                except ValueError:
                    pass
        if coll_name == "payload_armory":
            from crypto import encrypt_data
            if "username" in data and isinstance(data["username"], str):
                data["username"] = encrypt_data(data["username"])
            if "spam_message" in data and isinstance(data["spam_message"], str):
                data["spam_message"] = encrypt_data(data["spam_message"])
        await db_ref[coll_name].update_one({"_id": valid_id}, {"$set": data})
        logger.info(f"[Admin] Document updated: {coll_name}/{doc_id}")
    except json.JSONDecodeError as e:
        logger.error(f"[Admin] Invalid JSON in edit_doc: {e}")
        return HTMLResponse("Invalid JSON", status_code=400)
    except Exception as e:
        logger.error(f"[Admin] Error editing doc {coll_name}/{doc_id}: {e}")
        return HTMLResponse("Internal error", status_code=500)
    return RedirectResponse("/admin?tab=db", status_code=303)

@app.post("/admin/generate_key")
async def admin_generate_key(request: Request):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    form = await request.form()
    preset = form.get("duration_preset", "30")
    if preset == "custom":
        try:
            val = float(form.get("custom_val", 1))
            unit = form.get("custom_unit", "days")
            if unit == "minutes": days = val / 1440.0
            elif unit == "hours": days = val / 24.0
            elif unit == "days": days = val
            elif unit == "weeks": days = val * 7.0
            elif unit == "months": days = val * 30.0
            elif unit == "years": days = val * 365.0
            else: days = val
        except ValueError:
            days = 1.0
    else:
        try:
            days = float(preset)
        except ValueError:
            days = 30.0
    from premium import generate_license_key
    key = await generate_license_key(days)
    logger.info(f"[Admin] Generated license key: {key} (duration: {days} days)")
    return RedirectResponse("/admin?tab=keys", status_code=303)

@app.post("/admin/server/{guild_id}/toggle_premium")
async def admin_toggle_premium(request: Request, guild_id: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    from premium import is_guild_premium, grant_premium
    is_prem = await is_guild_premium(int(guild_id))
    if is_prem:
        await db.guild_premium.delete_one({"guild_id": guild_id})
        logger.info(f"[Admin] Removed premium from guild: {guild_id}")
    else:
        await grant_premium(guild_id, 30)
        logger.info(f"[Admin] Granted 30-day premium to guild: {guild_id}")
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/server/{guild_id}/reset_cooldowns")
async def admin_reset_cooldowns(request: Request, guild_id: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    await db.guild_cooldowns.delete_many({"guild_id": guild_id})
    logger.info(f"[Admin] Reset all cooldowns for guild: {guild_id}")
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/server/{guild_id}/reset_cooldown/{module}")
async def admin_reset_cooldown(request: Request, guild_id: str, module: str):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    await db.guild_cooldowns.delete_one({"guild_id": guild_id, "raid_type": module})
    logger.info(f"[Admin] Reset cooldown for module '{module}' in guild: {guild_id}")
    return RedirectResponse("/admin?tab=servers", status_code=303)

@app.post("/admin/gift_premium")
async def admin_gift_premium(request: Request):
    if not await check_admin_auth(request):
        return RedirectResponse("/")
    form = await request.form()
    guild_id = form.get("guild_id")
    days = int(form.get("days", "30")) if form.get("days", "30").isdigit() else 30
    from premium import grant_premium
    if guild_id:
        await grant_premium(guild_id, days)
        await db.gift_logs.insert_one({
            "guild_id": guild_id,
            "days": days,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "admin_ip": request.client.host
        })
        logger.info(f"[Admin] Gifted {days} days of premium to guild: {guild_id}")
        return RedirectResponse(f"/admin?tab=keys&msg=Successfully+gifted+{days}+days+to+{guild_id}", status_code=303)
    return RedirectResponse("/admin?tab=keys", status_code=303)
