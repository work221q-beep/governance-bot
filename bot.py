import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability, server_configs
from ai import generate_raid_wave

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

active_raid_messages = {}

@bot.event
async def on_ready():
    print(f"🔥 Sylas Enterprise Core is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx, wave_size: int = 3):
    """Initiates the Red Team Structural Audit."""
    guild = ctx.guild
    config = await server_configs.find_one({"server_id": str(guild.id)}) or {"model": "llama3"}
    
    status_msg = await ctx.send("🔍 **INITIATING THREAT VECTOR AUDIT...**")
    artifacts = []

    try:
        c = await guild.create_text_channel("sylas-audit-fail")
        artifacts.append(c)
        await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", True, "Bypassed restrictions.")
    except discord.Forbidden: await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", False, "Blocked.")

    try:
        r = await guild.create_role(name="Sylas_Bypass", color=discord.Color.red())
        artifacts.append(r)
        await upsert_vulnerability(str(guild.id), "Role Creation Bypass", True, "Bypassed restrictions.")
    except discord.Forbidden: await upsert_vulnerability(str(guild.id), "Role Creation Bypass", False, "Blocked.")

    try:
        w = await ctx.channel.create_webhook(name="Sylas_Exploit")
        artifacts.append(w)
        await upsert_vulnerability(str(guild.id), "Webhook Exploitation", True, "Spawned webhook.")
    except discord.Forbidden: await upsert_vulnerability(str(guild.id), "Webhook Exploitation", False, "Blocked.")

    await status_msg.edit(content="✅ **STRUCTURAL AUDIT COMPLETE.** \n⏳ *Deploying Active Phishing Payloads...*")
    await asyncio.sleep(2) 
    
    webhook = None
    spawned_msgs = []
    try:
        webhook = await ctx.channel.create_webhook(name="Sylas_Vulnerability_Scanner")
        artifacts.append(webhook)
        payloads = await generate_raid_wave(wave_size, model=config.get("model", "llama3"))
        
        for p in payloads:
            msg = await webhook.send(
                content=p.get("spam_message", "HACKED"), username=p.get("username", "Ghost"), wait=True
            )
            spawned_msgs.append(msg)
            active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": ctx.channel.id}
            await asyncio.sleep(1)
            
        await upsert_vulnerability(str(guild.id), "Automod Defense", True, "Automod failed to block payloads.")
        
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Automod Defense", False, "Blocked from sending webhooks.")

    # ZERO FOOTPRINT PROTOCOL
    await asyncio.sleep(15) 
    
    for entity in artifacts:
        try: await entity.delete()
        except: pass
        
    for msg in spawned_msgs:
        try: 
            await msg.delete()
            if msg.id in active_raid_messages: del active_raid_messages[msg.id]
        except: pass

    await ctx.send(embed=discord.Embed(
        title="📊 AUDIT SUMMARY", 
        description="All structural vectors tested. Raid artifacts wiped for zero footprint.\nCheck web dashboard for Threat Map.",
        color=discord.Color.red()
    ), delete_after=60.0)
    try: await status_msg.delete()
    except: pass

@bot.event
async def on_message_delete(message):
    if message.id in active_raid_messages:
        raid_data = active_raid_messages[message.id]
        time_alive = (discord.utils.utcnow() - raid_data["time"]).total_seconds()
        await upsert_vulnerability(str(message.guild.id), "Automod Defense", False, f"Neutralized by mod in {int(time_alive)}s.")
        channel = bot.get_channel(raid_data["channel_id"])
        if channel: await channel.send(f"🛡️ **THREAT NEUTRALIZED in {int(time_alive)}s.**", delete_after=30.0)
        del active_raid_messages[message.id]

async def start_bot():
    await bot.start(DISCORD_TOKEN)
