import os, asyncio, discord
from discord.ext import commands
from db import log_probe
from ai import generate_raid_wave

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

active_raid_messages = {} # Tracks messages to score mods when they delete them

@bot.event
async def on_ready():
    print(f"🔥 Sylas Chaos Engine is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx, wave_size: int = 5):
    guild = ctx.guild
    await ctx.send(f"⚠️ **RED TEAM TEST INITIATED.** Emulating {wave_size} hostile actors...")

    # --- THE PHALANX: WEBHOOK SPAM TEST ---
    try:
        webhook = await ctx.channel.create_webhook(name="Sylas_Vulnerability_Scanner")
        payloads = await generate_raid_wave(wave_size)
        
        for p in payloads:
            msg = await webhook.send(
                content=p.get("spam_message", "HACKED"), 
                username=p.get("username", "Ghost_User"),
                avatar_url="https://i.imgur.com/vHq0A6y.png",
                wait=True
            )
            active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "modded": False}
            await asyncio.sleep(2) # Protects Render CPU from rate-limit spikes
            
        await webhook.delete()
        await log_probe(str(guild.id), "Automod/Spam Defense", "COMPLETE", f"Fired {wave_size} payloads.")
    except discord.Forbidden:
        await ctx.send("🛡️ **PASS:** I am not allowed to create Webhooks here.")

    # --- THE INQUISITOR: PERMISSION PROBING ---
    await ctx.send("🔍 **PROBING SERVER PERMISSIONS...**")
    await asyncio.sleep(1)

    # Probe 1: Unauthorized Channel Creation
    try:
        hacked_channel = await guild.create_text_channel("sylas-audit-fail")
        await ctx.send("🚨 **FAIL:** I successfully created a channel! Fix your `@everyone` permissions.")
        await log_probe(str(guild.id), "Unauthorized Channel Creation", "FAIL", "Channel created.")
        await hacked_channel.delete()
    except discord.Forbidden:
        await ctx.send("🛡️ **PASS:** Unauthorized channel creation blocked.")

    # Probe 2: Unauthorized Role Spam
    try:
        hacked_role = await guild.create_role(name="Bypass_Test", color=discord.Color.red())
        await ctx.send("🚨 **CRITICAL FAIL:** I successfully created a role!")
        await log_probe(str(guild.id), "Unauthorized Role Creation", "FAIL", "Role created.")
        await hacked_role.delete()
    except discord.Forbidden:
        await ctx.send("🛡️ **PASS:** Unauthorized role creation blocked.")

    await ctx.send("🏁 **STRESS TEST COMPLETE.** Tracking mod response time for active spam...")

# --- THE WARDEN: MODERATOR TRACKING ---
@bot.event
async def on_message_delete(message):
    if message.id in active_raid_messages:
        time_alive = (discord.utils.utcnow() - active_raid_messages[message.id]["time"]).total_seconds()
        # Fetch the audit log to see WHICH mod deleted it
        async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=1):
            if entry.target.id == message.author.id:
                mod = entry.user
                await message.channel.send(f"🛡️ **THREAT NEUTRALIZED.** {mod.mention} deleted the spam in {int(time_alive)} seconds.")
                del active_raid_messages[message.id]
                break

async def start_bot():
    await bot.start(DISCORD_TOKEN)
