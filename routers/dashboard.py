import httpx
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from routers.dependencies import require_auth
import crypto

dashboard_router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="templates")

@dashboard_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, auth_context: dict = Depends(require_auth)):
    user_data = auth_context["user_data"]
    session = auth_context["session"]
    
    access_token = session["user_data"].get("access_token")
    user_guilds = []
    
    if access_token:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {crypto.decrypt_data(access_token)}"}
            response = await client.get("https://discord.com/api/users/@me/guilds", headers=headers)
            
            if response.status_code == 200:
                raw_guilds = response.json()
                for g in raw_guilds:
                    permissions = int(g.get("permissions", 0))
                    if (permissions & 0x8) == 0x8 or (permissions & 0x20) == 0x20:
                        user_guilds.append(g)

    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "user": user_data, 
        "guilds": user_guilds,
        "csrf_token": session.get("csrf_token")
    })