import os
import urllib.parse
import secrets
import re
import datetime
import httpx
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
import crypto
from db import db

auth_router = APIRouter(tags=["authentication"])

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

@auth_router.get("/login")
async def login(request: Request, next_url: str = None):
    # SECURITY FIX: Strict regex ensures URL is relative, defeating /\ bypasses
    if next_url and not re.match(r'^/[a-zA-Z0-9_\-\?&=/]*$', next_url): 
        next_url = None
        
    # SECURITY FIX: Cryptographic state token to prevent Login CSRF
    oauth_state = secrets.token_urlsafe(32)
    encoded_next_url = urllib.parse.quote(next_url, safe="") if next_url else "none"
    state = f"login_{encoded_next_url}_{oauth_state}"
    
    encoded_uri = urllib.parse.quote(DISCORD_REDIRECT_URI, safe="")
    url = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&response_type=code&redirect_uri={encoded_uri}&scope=identify%20guilds&state={urllib.parse.quote(state)}"
    
    response = RedirectResponse(url)
    response.set_cookie("oauth_state", oauth_state, httponly=True, secure=True, max_age=300)
    return response

@auth_router.get("/auth/callback")
async def callback(request: Request, code: str = None, error: str = None, state: str = None):
    if error or not code: 
        return RedirectResponse(url="/login")

    redirect_url = "/dashboard"
    
    # SECURITY FIX: Verify state token matches cookie to prevent CSRF
    if state and state.startswith("login_"):
        parts = state.split("_", 2)
        expected_state = request.cookies.get("oauth_state")
        
        if len(parts) != 3 or parts[2] != expected_state:
            return RedirectResponse(url="/login?error=csrf_validation_failed")
            
        if parts[1] != "none":
            parsed_url = urllib.parse.unquote(parts[1])
            if re.match(r'^/[a-zA-Z0-9_\-\?&=/]*$', parsed_url): 
                redirect_url = parsed_url

    # Token Exchange
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI
    }
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post("https://discord.com/api/oauth2/token", data=data)
        if token_response.status_code != 200:
            return RedirectResponse(url="/login?error=token_exchange_failed")
            
        token_json = token_response.json()
        access_token = token_json.get("access_token")
        
        user_response = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        if user_response.status_code != 200:
            return RedirectResponse(url="/login?error=user_fetch_failed")
            
        user_json = user_response.json()

    # Session Creation
    session_id = secrets.token_urlsafe(64)
    csrf_token = secrets.token_urlsafe(32)
    
    user_data = {
        "id": crypto.encrypt_data(user_json["id"]),
        "username": crypto.encrypt_data(user_json["username"]),
        "avatar": crypto.encrypt_data(user_json.get("avatar", "")),
        "access_token": crypto.encrypt_data(access_token)
    }

    await db.sessions.insert_one({
        "session_id": session_id,
        "csrf_token": csrf_token,
        "user_data": user_data,
        "created_at": datetime.datetime.utcnow(),
        "expires_at": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    })

    response = RedirectResponse(url=redirect_url)
    response.set_cookie("session_id", session_id, httponly=True, secure=True, max_age=604800)
    response.delete_cookie("oauth_state", path="/")
    return response

# SECURITY FIX: Strict POST enforcement to prevent Nuisance CSRF
@auth_router.post("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id: 
        await db.sessions.delete_one({"session_id": session_id})
        
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_id", path="/")
    response.delete_cookie("admin_auth", path="/")
    return response