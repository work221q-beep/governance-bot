import os
import httpx
import crypto
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from db import db
import hmac

# Core Authentication Dependency
async def get_session_user(request: Request):
    """Extracts session, validates existence, and decrypts user data securely."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
        
    session = await db.sessions.find_one({"session_id": session_id})
    if not session:
        return None
        
    user_data = session.get("user_data")
    if user_data:
        try:
            # Enforce Cryptographic Integrity
            user_data["id"] = crypto.decrypt_data(user_data["id"])
            user_data["username"] = crypto.decrypt_data(user_data["username"])
            user_data["avatar"] = crypto.decrypt_data(user_data["avatar"])
        except Exception as e:
            print(f"[SECURITY] Decryption failed for session {session_id}: {e}")
            return None
            
    return {"session": session, "user_data": user_data}

# Route Protector Dependency
async def require_auth(user_dict: dict = Depends(get_session_user)):
    """Forces redirect if the user is unauthenticated."""
    if not user_dict or not user_dict.get("user_data"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user_dict

# Admin Auth Dependency
async def check_admin_auth(request: Request):
    """Validates Master Admin session and returns True/False."""
    admin_auth = request.cookies.get("admin_auth")
    if not admin_auth:
        return False
    session = await db.admin_sessions.find_one({"token": admin_auth})
    if not session:
        return False
    return True

# Guild Permission Dependency
async def check_guild_permission(user_data: dict, guild_id: str) -> bool:
    """Checks if the authenticated user has Manage Server or Admin rights in the guild."""
    access_token = user_data.get("access_token")
    if not access_token:
        return False
        
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {crypto.decrypt_data(access_token)}"}
        response = await client.get("https://discord.com/api/users/@me/guilds", headers=headers)
        
        if response.status_code != 200:
            return False
            
        guilds = response.json()
        for g in guilds:
            if str(g["id"]) == str(guild_id):
                # Check for Administrator (0x8) or Manage Server (0x20)
                permissions = int(g.get("permissions", 0))
                if (permissions & 0x8) == 0x8 or (permissions & 0x20) == 0x20:
                    return True
        return False