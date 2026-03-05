from datetime import datetime, timedelta
from db import guild_premium, guild_cooldowns

PREMIUM_FEATURES = ["fake_mod", "insider_threat", "escalation", "harassment"]
FREE_FEATURES = ["phishing", "spam_flood"]

async def is_guild_premium(guild_id: int) -> bool:
    """Checks if a server has an active premium subscription."""
    sub = await guild_premium.find_one({"guild_id": str(guild_id)})
    if not sub:
        return False
    
    # Check if expired
    if datetime.utcnow() > sub["expires_at"]:
        await guild_premium.delete_one({"guild_id": str(guild_id)})
        return False
        
    return True

async def grant_premium(guild_id: str, days: int):
    """Grants or extends premium for a server."""
    existing = await guild_premium.find_one({"guild_id": str(guild_id)})
    now = datetime.utcnow()
    
    if existing and existing["expires_at"] > now:
        new_expiry = existing["expires_at"] + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
        
    await guild_premium.update_one(
        {"guild_id": str(guild_id)},
        {"$set": {"expires_at": new_expiry, "updated_at": now}},
        upsert=True
    )

async def check_and_set_cooldown(guild_id: int, raid_type: str, is_premium: bool) -> tuple[bool, str]:
    """
    Checks if a wargame is on cooldown. If not, sets the cooldown.
    Returns (Is_Allowed, Time_Remaining_String)
    """
    cooldown_hours = 4 if is_premium else 24
    
    record = await guild_cooldowns.find_one({"guild_id": str(guild_id), "raid_type": raid_type})
    now = datetime.utcnow()
    
    if record:
        time_since_last = now - record["last_used"]
        cooldown_delta = timedelta(hours=cooldown_hours)
        
        if time_since_last < cooldown_delta:
            time_left = cooldown_delta - time_since_last
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            return False, f"{hours}h {minutes}m"
            
    # If allowed, update the timestamp
    await guild_cooldowns.update_one(
        {"guild_id": str(guild_id), "raid_type": raid_type},
        {"$set": {"last_used": now}},
        upsert=True
    )
    return True, ""
