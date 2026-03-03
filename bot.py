import os
import re
import discord
from discord.ext import commands
from datetime import datetime
from db import ensure_player, players, events
from ai import arbitrate_claim

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BASE_URL = os.getenv("BASE_URL", "https://your-render-app.onrender.com")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

USER_COOLDOWN_SECONDS = 30
PAIR_COOLDOWN_SECONDS = 120

user_last_event = {}
pair_last_event = {}

CLAIM_REGEX = re.compile(
    r"\b(i beat|i destroyed|i 3-0d|i smoked)\b",
    re.IGNORECASE
)


@bot.event
async def on_ready():
    print(f"StatusCore online as {bot.user}")


# ✅ Leaderboard Command
@bot.command(name="leaderboard", aliases=["lb", "stats", "board"])
async def leaderboard_cmd(ctx):
    server_id = str(ctx.guild.id)
    url = f"{BASE_URL}/server/{server_id}/leaderboard"

    embed = discord.Embed(
        title="STATUSCORE | LIVE ARENA",
        description=f"**[Click here to view the official ledger]({url})**",
        color=0xff0000
    )
    embed.set_footer(text="Lying is expensive. Check the board.")

    await ctx.send(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if not CLAIM_REGEX.search(message.content):
        await bot.process_commands(message)
        return

    if not message.mentions:
        await bot.process_commands(message)
        return

    server_id = str(message.guild.id)

    actor_id = str(message.author.id)
    actor_name = message.author.display_name

    target = message.mentions[0]
    target_id = str(target.id)
    target_name = target.display_name

    now = datetime.utcnow()

    # USER COOLDOWN
    last_time = user_last_event.get(actor_id)
    if last_time and (now - last_time).total_seconds() < USER_COOLDOWN_SECONDS:
        return
    user_last_event[actor_id] = now

    # PAIR COOLDOWN
    pair_key = f"{server_id}:{actor_id}:{target_id}"
    last_pair = pair_last_event.get(pair_key)
    if last_pair and (now - last_pair).total_seconds() < PAIR_COOLDOWN_SECONDS:
        return
    pair_last_event[pair_key] = now

    await ensure_player(server_id, actor_id, actor_name)
    await ensure_player(server_id, target_id, target_name)

    verdict = await arbitrate_claim(actor_name, target_name, message.content)

    if verdict["verdict"] == "valid":
        await players.update_one(
            {"server_id": server_id, "discord_id": actor_id},
            {"$inc": {"credibility": 5}, "$set": {"lastActive": now}}
        )

        await players.update_one(
            {"server_id": server_id, "discord_id": target_id},
            {"$inc": {"fraudIndex": 5}, "$set": {"lastActive": now}}
        )

        await message.reply(
            f"⚖️ VERIFIED. {actor_name} credibility +5. {target_name} fraud +5."
        )

    else:
        await players.update_one(
            {"server_id": server_id, "discord_id": actor_id},
            {"$inc": {"fraudIndex": 5}, "$set": {"lastActive": now}}
        )

        await message.reply(
            f"⚖️ INVALID CLAIM. {actor_name} fraud +5."
        )

    await events.insert_one({
        "server_id": server_id,
        "actor": actor_id,
        "target": target_id,
        "verdict": verdict["verdict"],
        "confidence": verdict["confidence"],
        "createdAt": now
    })

    await bot.process_commands(message)


async def start_bot():
    await bot.start(DISCORD_TOKEN)
