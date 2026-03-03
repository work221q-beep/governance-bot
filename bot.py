import os
import re
import discord
from discord.ext import commands
from datetime import datetime
from db import ensure_player, players, events
from ai import arbitrate_claim

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------
# Cooldowns
# ------------------

USER_COOLDOWN_SECONDS = 30
PAIR_COOLDOWN_SECONDS = 120

user_last_event = {}
pair_last_event = {}

CLAIM_REGEX = re.compile(r"\b(i beat|i destroyed|i 3-0d|i smoked)\b", re.IGNORECASE)


@bot.event
async def on_ready():
    print(f"StatusCore online as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.guild:
        return

    if not CLAIM_REGEX.search(message.content):
        return

    if not message.mentions:
        return

    actor_id = str(message.author.id)
    actor_name = message.author.display_name
    target = message.mentions[0]
    target_id = str(target.id)
    target_name = target.display_name

    now = datetime.utcnow()

    # ---- User cooldown
    last_time = user_last_event.get(actor_id)
    if last_time and (now - last_time).total_seconds() < USER_COOLDOWN_SECONDS:
        return

    user_last_event[actor_id] = now

    # ---- Pair cooldown
    pair_key = f"{actor_id}:{target_id}"
    last_pair = pair_last_event.get(pair_key)

    if last_pair and (now - last_pair).total_seconds() < PAIR_COOLDOWN_SECONDS:
        return

    pair_last_event[pair_key] = now

    # ---- Ensure players
    actor = await ensure_player(actor_id, actor_name)
    target_player = await ensure_player(target_id, target_name)

    verdict = await arbitrate_claim(actor_name, target_name, message.content)

    if verdict["verdict"] == "valid":
        await players.update_one(
            {"discord_id": actor_id},
            {"$inc": {"credibility": 5},
             "$set": {"lastActive": now}}
        )

        await players.update_one(
            {"discord_id": target_id},
            {"$inc": {"fraudIndex": 5},
             "$set": {"lastActive": now}}
        )

        await message.reply(
            f"⚖️ VERIFIED. {actor_name} gains credibility. {target_name} fraud +5."
        )

    else:
        await players.update_one(
            {"discord_id": actor_id},
            {"$inc": {"fraudIndex": 5},
             "$set": {"lastActive": now}}
        )

        await message.reply(
            f"⚖️ INVALID CLAIM. {actor_name} fraud +5."
        )

    await events.insert_one({
        "actor": actor_id,
        "target": target_id,
        "verdict": verdict["verdict"],
        "confidence": verdict["confidence"],
        "createdAt": now
    })


async def start_bot():
    await bot.start(DISCORD_TOKEN)
