import os
import hmac
import datetime
import secrets
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from db import db
import premium
from routers.dependencies import check_admin_auth

admin_router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")
ADMIN_KEY = os.getenv("ADMIN_KEY", "fallback_dev_key")

@admin_router.get("/")
async def admin_dashboard(request: Request):
    if not await check_admin_auth(request):
        return templates.TemplateResponse("admin.html", {"request": request, "authenticated": False})
        
    stats = {
        "total_users": await db.sessions.count_documents({}),
        "premium_guilds": await db.premium_guilds.count_documents({}),
    }
    
    # Send the CSRF token to the template for inclusion in forms
    admin_csrf = request.cookies.get("admin_csrf", "")
    return templates.TemplateResponse("admin.html", {"request": request, "authenticated": True, "stats": stats, "csrf_token": admin_csrf})

@admin_router.post("/auth")
async def admin_auth_post(request: Request, key: str = Form(...)):
    if hmac.compare_digest(key, ADMIN_KEY):
        response = RedirectResponse("/admin", status_code=303)
        token = secrets.token_urlsafe(64)
        admin_csrf = secrets.token_urlsafe(32) # SECURITY FIX: Master Admin CSRF Token
        
        await db.admin_sessions.insert_one({ 
            "token": token, 
            "csrf_token": admin_csrf,
            "created_at": datetime.datetime.utcnow() 
        })
        
        response.set_cookie("admin_auth", token, httponly=True, secure=True, max_age=86400)
        response.set_cookie("admin_csrf", admin_csrf, secure=True, httponly=True)
        return response
        
    return HTMLResponse("Uplink Severed: Invalid Signature", status_code=403)

@admin_router.post("/purge_armory")
async def admin_purge_armory(request: Request, csrf_token: str = Form(...)):
    if not await check_admin_auth(request): return RedirectResponse("/")
    
    # SECURITY FIX: Validate CSRF for destructive actions
    expected_csrf = request.cookies.get("admin_csrf")
    if not expected_csrf or not hmac.compare_digest(csrf_token, expected_csrf):
        raise HTTPException(status_code=403, detail="Admin CSRF token mismatch")
        
    await db.payload_armory.delete_many({})
    return RedirectResponse("/admin?success=Armory Purged", status_code=303)

@admin_router.post("/gift_premium")
async def admin_gift_premium(request: Request, guild_id: str = Form(...), days: int = Form(...), csrf_token: str = Form(...)):
    if not await check_admin_auth(request): return RedirectResponse("/")
    
    # SECURITY FIX: Validate CSRF 
    expected_csrf = request.cookies.get("admin_csrf")
    if not expected_csrf or not hmac.compare_digest(csrf_token, expected_csrf):
        raise HTTPException(status_code=403, detail="Admin CSRF token mismatch")
        
    await premium.grant_premium(guild_id, days)
    return RedirectResponse(f"/admin?success=Granted Premium to {guild_id}", status_code=303)