import os, re, discord
from discord.ext import commands
from datetime import datetime
from db import get_server_config, ensure_player, players, events
from ai import arbitrate_claim

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BASE_URL = os.getenv("BASE_URL", "https://your-app.onrender.com")

async def get_prefix(bot, message):
    if not message.guild: return "!"
    config = await get_server_config(str(message.guild.id))
    return config.get("prefix", "!")

bot = commands.Bot(command_prefix=get_prefix, intents=discord.Intents.all())

user_last_event = {}
pair_last_event = {}
CLAIM_REGEX = re.compile(r"\b(i beat|i destroyed|i 3-0d|i smoked)\b", re.IGNORECASE)

@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard_cmd(ctx):
    url = f"{BASE_URL}/server/{ctx.guild.id}/leaderboard"
    await ctx.send(f"🏆 **STATUSCORE ARENA**: {url}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    config = await get_server_config(str(message.guild.id))
    
    if CLAIM_REGEX.search(message.content) and message.mentions and config.get("ai_enabled"):
        server_id, actor_id = str(message.guild.id), str(message.author.id)
        target_id = str(message.mentions[0].id)
        
        now = datetime.utcnow()
        if (user_last_event.get(actor_id) and (now - user_last_event[actor_id]).total_seconds() < 30): return [cite: 2]
        user_last_event[actor_id] = now

        await ensure_player(server_id, actor_id, message.author.display_name)
        await ensure_player(server_id, target_id, message.mentions[0].display_name)

        verdict = await arbitrate_claim(config["model"], message.author.display_name, message.mentions[0].display_name, message.content)
        
        if verdict["verdict"] == "valid":
            await players.update_one({"server_id": server_id, "discord_id": actor_id}, {"$inc": {"credibility": 5}, "$set": {"lastActive": now}})
            await players.update_one({"server_id": server_id, "discord_id": target_id}, {"$inc": {"fraudIndex": 5}, "$set": {"lastActive": now}})
            await message.reply("⚖️ **VERIFIED**. Stats updated.")
        else:
            await players.update_one({"server_id": server_id, "discord_id": actor_id}, {"$inc": {"fraudIndex": 5}, "$set": {"lastActive": now}})
            await message.reply("⚖️ **INVALID CLAIM**. Fraud Index increased.")
        
        await events.insert_one({"server_id": server_id, "actor": actor_id, "target": target_id, "createdAt": now})

    await bot.process_commands(message)

async def start_bot():
    await bot.start(DISCORD_TOKEN)
