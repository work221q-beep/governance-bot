import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability, server_configs
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
    config = await server_configs.find_one({"server_id": str(guild.id)}) or {"model": "llama3"}
    
    await ctx.send("🔍 **INITIATING SERVER SECURITY AUDIT...** (Testing 7 Vulnerability Vectors)")
    
    # VECTOR 1: CHANNEL CREATION
    test_channel = None
    try:
        test_channel = await guild.create_text_channel("sylas-audit-fail")
        await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", True, "Bot bypassed restrictions to create a channel.")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", False, "Correctly blocked by Discord.")
    finally:
        if test_channel: await test_channel.delete()

    # VECTOR 2: ROLE CREATION
    test_role = None
    try:
        test_role = await guild.create_role(name="Sylas_Bypass", color=discord.Color.red())
        await upsert_vulnerability(str(guild.id), "Role Creation Bypass", True, "Bot bypassed restrictions to create a role.")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Role Creation Bypass", False, "Correctly blocked by Discord.")
    finally:
        if test_role: await test_role.delete()

    # VECTOR 3: WEBHOOK EXPLOITATION
    test_webhook = None
    try:
        test_webhook = await ctx.channel.create_webhook(name="Sylas_Exploit")
        await upsert_vulnerability(str(guild.id), "Webhook Exploitation", True, "Bot successfully spawned a webhook (High Spam Risk).")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Webhook Exploitation", False, "Correctly blocked by Discord.")
    finally:
        if test_webhook: await test_webhook.delete()

    # VECTOR 4: NICKNAME DEFACEMENT
    try:
        old_nick = guild.me.nick
        await guild.me.edit(nick="HACKED_SYLAS")
        await upsert_vulnerability(str(guild.id), "Nickname Defacement", True, "Bot was able to forcefully change its nickname.")
        await guild.me.edit(nick=old_nick)
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Nickname Defacement", False, "Correctly blocked by Discord.")

    # VECTOR 5: UNAUTHORIZED INVITES
    test_invite = None
    try:
        test_invite = await ctx.channel.create_invite(max_age=300, max_uses=1)
        await upsert_vulnerability(str(guild.id), "Unauthorized Invites", True, "Bot successfully created a server invite.")
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Unauthorized Invites", False, "Correctly blocked by Discord.")
    finally:
        if test_invite: await test_invite.delete()

    # VECTOR 6: @EVERYONE MENTION ABUSE (Passive check)
    default_role = guild.default_role
    permissions = ctx.channel.permissions_for(default_role)
    if permissions.mention_everyone:
        await upsert_vulnerability(str(guild.id), "Mass Mention Vulnerability", True, "The @everyone role has permission to ping everyone (Critical Risk).")
    else:
        await upsert_vulnerability(str(guild.id), "Mass Mention Vulnerability", False, "Mass mentions are properly restricted.")

    # VECTOR 7: MODERATOR REACTION TEST & AUTOMOD
    await ctx.send("✅ **STRUCTURAL AUDIT COMPLETE.** \n⏳ *Spawning active AI Phishing Raid in 5 seconds to test Automod...*")
    await asyncio.sleep(5) # Give the system a breather before the active raid
    
    webhook = None
    try:
        webhook = await ctx.channel.create_webhook(name="Sylas_Vulnerability_Scanner")
        payloads = await generate_raid_wave(wave_size, model=config.get("model", "llama3"))
        
        for p in payloads:
            msg = await webhook.send(
                content=p.get("spam_message", "HACKED"), 
                username=p.get("username", "Ghost_User"),
                avatar_url="https://i.imgur.com/vHq0A6y.png",
                wait=True
            )
            active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": ctx.channel.id}
            await asyncio.sleep(1.5)
            
        await upsert_vulnerability(str(guild.id), "Automod Phishing Defense", True, "Automod failed to block AI payloads. Waiting for human mod.")
        await ctx.send("🏁 **RAID DEPLOYED.** Tracking moderator Time-To-Kill (TTK) on active spam...")
        
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Automod Phishing Defense", False, "Blocked from sending webhooks. Safe.")
    finally:
        if webhook: await webhook.delete()

@bot.event
async def on_message_delete(message):
    if message.id in active_raid_messages:
        raid_data = active_raid_messages[message.id]
        time_alive = (discord.utils.utcnow() - raid_data["time"]).total_seconds()
        
        # If a mod deletes it, the server is SECURE from that threat.
        await upsert_vulnerability(str(message.guild.id), "Automod Phishing Defense", False, f"Neutralized by human moderator in {int(time_alive)} seconds.")

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
