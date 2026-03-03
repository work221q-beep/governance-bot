import os
import asyncio
import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from bot import start_bot
from db import get_server_config, configs
from ai import get_available_models

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")

serializer = URLSafeSerializer(SECRET_KEY)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_bot())


@app.get("/")
async def home(request: Request):
    user_cookie = request.cookies.get("session")
    user = None

    if user_cookie:
        try:
            user = serializer.loads(user_cookie)
        except:
            user = None

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user}
    )


@app.get("/login")
async def login():
    url = (
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        "&scope=identify%20guilds"
    )
    return RedirectResponse(url)


@app.get("/auth/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        token_data = token_res.json()
        access_token = token_data.get("access_token")

        user_res = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )

        guild_res = await client.get(
            "https://discord.com/api/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"}
        )

        user = user_res.json()
        guilds = guild_res.json()

    manageable_guilds = []

    for guild in guilds:
        permissions = int(guild["permissions"])
        is_owner = guild["owner"]
        can_manage = permissions & 0x20

        if is_owner or can_manage:
            manageable_guilds.append(guild)

    session_data = {
        "id": user["id"],
        "username": user["username"],
        "guilds": manageable_guilds
    }

    response = RedirectResponse(url="/dashboard")
    response.set_cookie(
        "session",
        serializer.dumps(session_data),
        httponly=True
    )

    return response


@app.get("/dashboard")
async def dashboard(request: Request):
    user_cookie = request.cookies.get("session")

    if not user_cookie:
        return RedirectResponse("/")

    try:
        user = serializer.loads(user_cookie)
    except:
        return RedirectResponse("/")

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user": user}
    )


@app.get("/server/{guild_id}")
async def server_panel(request: Request, guild_id: str):
    user_cookie = request.cookies.get("session")

    if not user_cookie:
        return RedirectResponse("/")

    try:
        user = serializer.loads(user_cookie)
    except:
        return RedirectResponse("/")

    allowed = False
    guild_name = None

    for guild in user["guilds"]:
        if guild["id"] == guild_id:
            allowed = True
            guild_name = guild["name"]
            break

    if not allowed:
        return HTMLResponse("Access Denied", status_code=403)

    config = await get_server_config(guild_id)
    models = await get_available_models()

    return templates.TemplateResponse(
        "server.html",
        {
            "request": request,
            "guild_id": guild_id,
            "guild_name": guild_name,
            "config": config,
            "models": models
        }
    )


@app.post("/server/{guild_id}/update")
async def update_server(
    request: Request,
    guild_id: str,
    prefix: str = Form(...),
    temperature: float = Form(...),
    model: str = Form(...),
    ai_enabled: str = Form(None),
    respond_every_message: str = Form(None)
):
    user_cookie = request.cookies.get("session")

    if not user_cookie:
        return RedirectResponse("/")

    try:
        user = serializer.loads(user_cookie)
    except:
        return RedirectResponse("/")

    allowed = any(g["id"] == guild_id for g in user["guilds"])
    if not allowed:
        return HTMLResponse("Access Denied", status_code=403)

    await configs.update_one(
        {"server_id": guild_id},
        {
            "$set": {
                "prefix": prefix,
                "temperature": temperature,
                "model": model,
                "ai_enabled": bool(ai_enabled),
                "respond_every_message": bool(respond_every_message)
            }
        }
    )

    return RedirectResponse(f"/server/{guild_id}", status_code=303)
