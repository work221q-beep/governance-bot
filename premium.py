import secrets
import string
from datetime import datetime, timedelta
from db import guild_premium, guild_cooldowns, license_keys

PREMIUM_FEATURES = ["fake_mod", "insider_threat", "escalation", "harassment"]
FREE_FEATURES = ["phishing", "spam_flood"]

async def generate_license_key(days: int) -> str:
    """Generates a complex single-use license key valid for 24 hours."""
    alphabet = string.ascii_letters + string.digits
    key = "SYLAS-" + "".join(secrets.choice(alphabet) for _ in range(24))
    expires_at = datetime.utcnow() + timedelta(hours=24)
    await license_keys.insert_one({
        "key": key,
        "duration_days": days,
        "expires_at": expires_at,
        "used": False
    })
    return key

async def redeem_license_key(guild_id: str, key: str) -> bool:
    """Redeems a license key and grants premium to the server."""
    record = await license_keys.find_one({"key": key, "used": False})
    if not record:
        return False
    if datetime.utcnow() > record["expires_at"]:
        return False

    # Mark as used
    await license_keys.update_one({"_id": record["_id"]}, {"$set": {"used": True}})

    # Grant premium
    await grant_premium(guild_id, record["duration_days"])
    return True

async def is_guild_premium(guild_id: int) -> bool:
    """Checks if a server has an active premium subscription."""
    sub = await guild_premium.find_one({"guild_id": str(guild_id)})
    if not sub:
        return False

    # Check if expired
    if datetime.utcnow() > sub["expires_at"]:
        await guild_premium.delete_one({"guild_id": str(guild_id)})
        # Reset cooldowns for premium modules when premium expires
        await guild_cooldowns.delete_many({"guild_id": str(guild_id), "raid_type": {"$in": PREMIUM_FEATURES}})
        return False

    return True

async def get_premium_details(guild_id: str) -> dict:
    """Get premium details including days remaining."""
    sub = await guild_premium.find_one({"guild_id": str(guild_id)})
    if not sub:
        return None
    
    now = datetime.utcnow()
    if now > sub["expires_at"]:
        return None
    
    time_remaining = sub["expires_at"] - now
    days_remaining = time_remaining.days
    
    return {
        "expires_at": sub["expires_at"],
        "days_remaining": days_remaining,
        "is_active": True
    }

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
    # Reset cooldowns for all modules when premium is purchased
    await guild_cooldowns.delete_many({"guild_id": str(guild_id)})

async def check_cooldown(guild_id: int, raid_type: str, is_premium: bool) -> tuple[bool, str]:
    """
    Checks if a wargame is on cooldown.
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

    return True, ""

async def set_cooldown(guild_id: int, raid_type: str):
    """Sets the cooldown for a wargame."""
    now = datetime.utcnow()
    await guild_cooldowns.update_one(
        {"guild_id": str(guild_id), "raid_type": raid_type},
        {"$set": {"last_used": now}},
        upsert=True
    )
