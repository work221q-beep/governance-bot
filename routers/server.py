import discord
import urllib.parse
import datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from bot import bot
from db import db
from routers.dependencies import require_auth, check_guild_permission

server_router = APIRouter(prefix="/server/{guild_id}", tags=["server_management"])
templates = Jinja2Templates(directory="templates")

@server_router.get("/permissions")
async def server_permissions(request: Request, guild_id: str, tab: str = "roles", auth_context: dict = Depends(require_auth)):
    user_data = auth_context["user_data"]
    session = auth_context["session"]
    
    if not await check_guild_permission(user_data, guild_id):
        return RedirectResponse("/dashboard?error=Unauthorized")
        
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return RedirectResponse("/dashboard?error=Bot Not In Server")

    members = guild.members
    roles = guild.roles
    
    return templates.TemplateResponse("permissions.html", {
        "request": request,
        "guild": guild,
        "members": members,
        "roles": roles,
        "tab": tab,
        "user": user_data,
        "csrf_token": session.get("csrf_token")
    })

@server_router.post("/action/{action}/{target_id}")
async def mod_action(
    request: Request, 
    guild_id: str, 
    action: str, 
    target_id: str,
    csrf_token: str = Form(...),
    timeout_duration: int = Form(0),
    auth_context: dict = Depends(require_auth)
):
    user_data = auth_context["user_data"]
    session = auth_context["session"]
    
    # 1. CSRF Validation
    import hmac
    if not hmac.compare_digest(csrf_token, session.get("csrf_token", "")):
        return RedirectResponse(f"/server/{guild_id}/permissions?error=CSRF Validation Failed", status_code=303)
        
    # 2. Authorization
    if not await check_guild_permission(user_data, guild_id):
        return RedirectResponse("/dashboard", status_code=303)
        
    guild = bot.get_guild(int(guild_id))
    target = guild.get_member(int(target_id)) if guild else None
    if not target:
        return RedirectResponse(f"/server/{guild_id}/permissions?error=Target Not Found", status_code=303)

    audit_log_reason = f"Action via Sylas Web UI by {user_data.get('username')}"
    
    # 3. Execution (DoS Patched)
    try:
        if action == "kick": 
            await target.kick(reason=audit_log_reason)
        elif action == "ban": 
            await target.ban(reason=audit_log_reason)
        elif action == "timeout": 
            if timeout_duration > 40320 or timeout_duration < 1:
                raise ValueError("Duration exceeds 28-day API bounds")
            await target.timeout(
                discord.utils.utcnow() + datetime.timedelta(minutes=timeout_duration), 
                reason=audit_log_reason
            )
            
    # Catching generic HTTPException to prevent 500 crashes
    except (discord.Forbidden, discord.HTTPException, ValueError) as e: 
        error_safe = urllib.parse.quote(str(e)[:150])
        return RedirectResponse(f"/server/{guild_id}/permissions?error=Execution Failed: {error_safe}", status_code=303)
            
    return RedirectResponse(f"/server/{guild_id}/permissions?success=Action Executed", status_code=303)