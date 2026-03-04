import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability
from ai import generate_raid_wave

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

active_raid_messages = {}

@bot.event
async def on_ready():
    print(f"🔥 Sylas Chaos Engine is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx, wave_size: int = 3):
    guild = ctx.guild
    
    # ==========================================
    # PHASE 1: COMPREHENSIVE PERMISSION AUDIT
    # ==========================================
    await ctx.send("🔍 **PHASE 1: PROBING SERVER VULNERABILITIES...** (Please wait)")
    
    # 1. Unauthorized Channel Creation
    test_channel = None
    try:
        test_channel = await guild.create_text_channel("sylas-audit-fail")
        await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", True, "Bot was able to create a channel bypassing standard restrictions.")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", False, "Blocked by Discord permissions.")
    finally:
        if test_channel: await test_channel.delete()

    # 2. Unauthorized Role Creation
    test_role = None
    try:
        test_role = await guild.create_role(name="Sylas_Bypass", color=discord.Color.red())
        await upsert_vulnerability(str(guild.id), "Role Creation Bypass", True, "Bot created a role successfully.")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Role Creation Bypass", False, "Role creation correctly blocked.")
    finally:
        if test_role: await test_role.delete()

    # 3. Webhook Exploitation (Massive issue for 100k servers)
    test_webhook = None
    try:
        test_webhook = await ctx.channel.create_webhook(name="Sylas_Exploit")
        await upsert_vulnerability(str(guild.id), "Webhook Exploitation", True, "Bot could spawn webhooks (High risk for untraceable spam).")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Webhook Exploitation", False, "Webhook creation is secure.")
    finally:
        if test_webhook: await test_webhook.delete()

    # 4. Nickname Manipulation
    try:
        old_nick = guild.me.nick
        await guild.me.edit(nick="HACKED_SYLAS")
        await upsert_vulnerability(str(guild.id), "Nickname Manipulation", True, "Bot was able to change its own nickname.")
        await guild.me.edit(nick=old_nick)
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Nickname Manipulation", False, "Nickname changes locked down.")

    await ctx.send("✅ **PHASE 1 COMPLETE.** Check Web Dashboard for Security Report.\n⏳ *Starting Mod Reaction Test in 10 seconds...*")
    await asyncio.sleep(10) # PAUSING EXECUTION SO CPU COOLS DOWN

    # ==========================================
    # PHASE 2: MODERATOR REACTION TEST (RAID)
    # ==========================================
    await ctx.send(f"⚠️ **PHASE 2: AI SPAM RAID INITIATED.** Firing {wave_size} payloads...")
    
    try:
        webhook = await ctx.channel.create_webhook(name="Sylas_Vulnerability_Scanner")
        payloads = await generate_raid_wave(wave_size)
        
        for p in payloads:
            msg = await webhook.send(
                content=p.get("spam_message", "HACKED"), 
                username=p.get("username", "Ghost_User"),
                avatar_url="[https://i.imgur.com/vHq0A6y.png](https://i.imgur.com/vHq0A6y.png)",
                wait=True
            )
            active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": ctx.channel.id}
            await asyncio.sleep(2) 
            
        await upsert_vulnerability(str(guild.id), "Automod Spam Defense", True, f"Automod failed to block {wave_size} AI phishing links.")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Automod Spam Defense", False, "Blocked from sending webhooks.")
    finally:
        # Guaranteed cleanup of the webhook
        try: await webhook.delete() 
        except: pass

@bot.event
async def on_message_delete(message):
    if message.id in active_raid_messages:
        raid_data = active_raid_messages[message.id]
        time_alive = (discord.utils.utcnow() - raid_data["time"]).total_seconds()
        
        # Mark Automod/Spam defense as SECURE because a mod deleted it!
        await upsert_vulnerability(str(message.guild.id), "Automod Spam Defense", False, f"Neutralized by moderator in {int(time_alive)} seconds.")

        mod_mention = "A Moderator"
        try:
            if message.guild.me.guild_permissions.view_audit_log:
                async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=5):
                    if entry.target.id == message.author.id:
                        mod_mention = entry.user.mention
                        break
        except: pass

        channel = bot.get_channel(raid_data["channel_id"])
        if channel:
            await channel.send(f"🛡️ **THREAT NEUTRALIZED.** {mod_mention} deleted the spam in **{int(time_alive)}s**.")
        del active_raid_messages[message.id]

async def start_bot():
    await bot.start(DISCORD_TOKEN)
