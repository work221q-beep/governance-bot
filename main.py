import os, asyncio, httpx, discord
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bot import start_bot, bot 
from db import init_indexes, server_configs, vuln_state

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

@app.get("/server/{guild_id}")
async def server_panel(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    
    guild = bot.get_guild(int(guild_id))
    roles = []
    
    if guild:
        for r in reversed(guild.roles): 
            current_perms = [perm[0] for perm in r.permissions if perm[1] is True]
            roles.append({
                "id": str(r.id), 
                "name": r.name, 
                "color": str(r.color) if r.color.value != 0 else "#71717a",
                "current": current_perms,
                "is_everyone": r.name == "@everyone",
                "is_bot": r.managed
            })
    
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
            
            try:
                await r.edit(permissions=discord.Permissions(**new_kwargs), reason="Sylas Enterprise: Live Web Sync")
            except discord.Forbidden:
                pass # Can't edit roles higher than the bot

    return RedirectResponse(f"/server/{guild_id}", status_code=303)

@app.get("/server/{guild_id}/audit")
async def server_audit(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")
    if not user_cookie: return RedirectResponse("/")
    vulns = await vuln_state.find({"server_id": guild_id}).to_list(100)
    score = int((sum(1 for v in vulns if v["status"] == "SECURE") / len(vulns)) * 100) if vulns else 100
    return templates.TemplateResponse("leaderboard.html", {"request": request, "guild_id": guild_id, "vulns": vulns, "score": score})
