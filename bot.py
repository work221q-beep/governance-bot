import os
import discord
from discord.ext import commands
from db import get_server_config, logs
from ai import generate_ai_response
from datetime import datetime, timedelta

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory user cooldown (burst protection)
USER_COOLDOWN_SECONDS = 15
user_last_message = {}


@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.guild:
        return

    config = await get_server_config(str(message.guild.id))

    if not config["ai_enabled"]:
        return

    if config["allowed_channels"]:
        if str(message.channel.id) not in config["allowed_channels"]:
            return

    should_respond = False

    if config["respond_every_message"]:
        should_respond = True
    elif bot.user in message.mentions:
        should_respond = True

    if not should_respond:
        return

    # --- COOLDOWN CHECK ---
    now = datetime.utcnow()
    last_time = user_last_message.get(message.author.id)

    if last_time and (now - last_time).total_seconds() < USER_COOLDOWN_SECONDS:
        return  # silent ignore

    user_last_message[message.author.id] = now
    # -----------------------

    prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()

    if not prompt:
        return

    await message.channel.typing()

    ai_response = await generate_ai_response(
        config["model"],
        prompt,
        config["temperature"]
    )

    await message.reply(ai_response[:1900])

    await logs.insert_one({
        "server_id": str(message.guild.id),
        "user_input": prompt,
        "ai_output": ai_response,
        "timestamp": datetime.utcnow()
    })

    await bot.process_commands(message)


async def start_bot():
    await bot.start(DISCORD_TOKEN)
