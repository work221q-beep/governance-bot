import secrets
import string
import asyncio
from datetime import datetime, timedelta
from db import guild_premium, guild_cooldowns, license_keys

PREMIUM_FEATURES = ["fake_mod", "insider_threat", "escalation", "harassment"]
FREE_FEATURES = ["phishing", "spam_flood"]

# PROPER SECURITY FIX: Sharded Lock Pool
# Handles concurrency safely without leaking memory or crashing the event loop.
# This strictly resolves the issue where the bot freezes and fails to delete messages.
LOCK_SHARDS = 256
_shards = [asyncio.Lock() for _ in range(LOCK_SHARDS)]

def get_lock(guild_id: str):
    return _shards[hash(str(guild_id)) % LOCK_SHARDS]

async def generate_license_key(days: int) -> str:
    """Generates a complex single-use license key valid for 24 hours."""
    alphabet = string.ascii_letters + string.digits
    raw_key = "".join(secrets.choice(alphabet) for _ in range(32))
    key = f"SYLAS-{raw_key[:8]}-{raw_key[8:16]}-{raw_key[16:24]}-{raw_key[24:]}"
    expires_at = datetime.utcnow() + timedelta(hours=24)
    
    await license_keys.insert_one({
        "key": key,
        "duration_days": days,
        "expires_at": expires_at,
        "used": False,
        "used_by_guild": None
    })
    return key

async def redeem_license_key(guild_id: str, key: str) -> bool:
    """Redeems a license key and grants premium to the server."""
    record = await license_keys.find_one({"key": key, "used": False})
    if not record:
        return False
        
    exp = record.get("expires_at")
    if isinstance(exp, str):
        try: exp = datetime.fromisoformat(exp)
        except ValueError: exp = datetime.utcnow() - timedelta(days=1)
    elif not isinstance(exp, datetime):
        exp = datetime.utcnow() - timedelta(days=1)
            
    if datetime.utcnow() > exp:
        return False
    
    # Atomic state lock to prevent double-spending
    update_result = await license_keys.update_one(
        {"_id": record["_id"], "used": False}, 
        {"$set": {"used": True, "used_by_guild": str(guild_id)}}
    )
    
    if update_result.modified_count == 0:
        return False
    
    await grant_premium(guild_id, record["duration_days"])
    return True

async def is_guild_premium(guild_id: int) -> bool:
    """Checks if a server has an active premium subscription."""
    sub = await guild_premium.find_one({"guild_id": str(guild_id)})
    if not sub:
        return False
    
    exp = sub.get("expires_at")
    
    if isinstance(exp, str):
        try: exp = datetime.fromisoformat(exp)
        except ValueError: return False
    elif not isinstance(exp, datetime):
        return False
            
    if datetime.utcnow() > exp:
        await guild_premium.delete_one({"guild_id": str(guild_id)})
        await guild_cooldowns.delete_many({"guild_id": str(guild_id), "raid_type": {"$in": PREMIUM_FEATURES}})
        return False
        
    return True

async def grant_premium(guild_id: str, days: int):
    """Grants or extends premium for a server atomically."""
    str_guild_id = str(guild_id)
        
    async with get_lock(str_guild_id):
        existing = await guild_premium.find_one({"guild_id": str_guild_id})
        now = datetime.utcnow()
        
        if existing and "expires_at" in existing:
            exp = existing["expires_at"]
            if isinstance(exp, str):
                try: exp = datetime.fromisoformat(exp)
                except ValueError: exp = now
            elif not isinstance(exp, datetime):
                exp = now
            
            if exp > now:
                new_expiry = exp + timedelta(days=days)
            else:
                new_expiry = now + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)
            
        await guild_premium.update_one(
            {"guild_id": str_guild_id},
            {"$set": {"expires_at": new_expiry, "updated_at": now}},
            upsert=True
        )
        
        await guild_cooldowns.delete_many({"guild_id": str_guild_id})

async def check_cooldown(guild_id: int, raid_type: str, is_premium: bool) -> tuple[bool, str]:
    """Checks if a wargame is on cooldown. Returns (Is_Allowed, Time_Remaining_String)"""
    cooldown_hours = 4 if is_premium else 24
    
    record = await guild_cooldowns.find_one({"guild_id": str(guild_id), "raid_type": raid_type})
    now = datetime.utcnow()
    
    if record:
        last_used = record.get("last_used", now)
        if isinstance(last_used, str):
            try: last_used = datetime.fromisoformat(last_used)
            except ValueError: last_used = now
        elif not isinstance(last_used, datetime):
            last_used = now
            
        time_since_last = now - last_used
        cooldown_delta = timedelta(hours=cooldown_hours)
        
        if time_since_last < cooldown_delta:
            time_left = cooldown_delta - time_since_last
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            return False, f"{hours}h {minutes}m"
            
    return True, ""

async def set_cooldown(guild_id: int, raid_type: str):
    """Sets the cooldown for a wargame."""
    now = datetime.utcnow()
    await guild_cooldowns.update_one(
        {"guild_id": str(guild_id), "raid_type": raid_type},
        {"$set": {"last_used": now}},
        upsert=True
    )