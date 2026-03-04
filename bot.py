import os, asyncio, discord
from discord.ext import commands
from db import log_probe
from ai import generate_raid_wave

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Tracks messages: {message_id: {"time": timestamp, "channel_id": id}}
active_raid_messages = {}

@bot.event
async def on_ready():
    print(f"🔥 Sylas Chaos Engine is online.")
    # Start a background task to clean up old tracked messages every hour
    bot.loop.create_task(raid_memory_cleanup())

async def raid_memory_cleanup():
    """Prevents 0.1 vCPU Render monolith from running out of RAM."""
    while not bot.is_closed():
        await asyncio.sleep(3600) # Run once an hour
        now = discord.utils.utcnow()
        # Remove anything older than 2 hours that wasn't deleted
        expired = [m_id for m_id, data in active_raid_messages.items() 
                   if (now - data["time"]).total_seconds() > 7200]
        for m_id in expired:
            del active_raid_messages[m_id]

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
            # Store channel_id so we can respond in the right place even if the object is lost
            active_raid_messages[msg.id] = {
                "time": discord.utils.utcnow(), 
                "channel_id": ctx.channel.id
            }
            await asyncio.sleep(2) 
            
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

# --- THE WARDEN: RESILIENT MODERATOR TRACKING ---
@bot.event
async def on_message_delete(message):
    # Use message.id to check if it's part of our active raid
    if message.id in active_raid_messages:
        raid_data = active_raid_messages[message.id]
        time_alive = (discord.utils.utcnow() - raid_data["time"]).total_seconds()
        
        # Default fallback if Audit Log is slow or inaccessible
        mod_mention = "A Moderator"

        try:
            # Check if we have permission to view audit logs
            if message.guild.me.guild_permissions.view_audit_log:
                async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=5):
                    # Match the deleted message's author (the Webhook)
                    if entry.target.id == message.author.id:
                        mod_mention = entry.user.mention
                        break
        except Exception as e:
            print(f"Audit Log Lookup Failed: {e}")

        # Send notification to the channel where the raid happened
        channel = bot.get_channel(raid_data["channel_id"])
        if channel:
            await channel.send(f"🛡️ **THREAT NEUTRALIZED.** {mod_mention} deleted the spam in **{int(time_alive)}s**.")
        
        # Clean up memory
        del active_raid_messages[message.id]

async def start_bot():
    await bot.start(DISCORD_TOKEN)
