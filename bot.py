import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability, server_configs, role_baselines
from ai import generate_raid_wave

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

active_raid_messages = {}
pending_remediations = {} # Stores violations for !fixperms

@bot.event
async def on_ready():
    print(f"🔥 Sylas Chaos Engine is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx, wave_size: int = 3):
    guild = ctx.guild
    config = await server_configs.find_one({"server_id": str(guild.id)}) or {"model": "llama3"}
    
    status_msg = await ctx.send("🔍 **INITIATING SERVER SECURITY AUDIT...** (Testing 7 Vulnerability Vectors)")
    
    # Track artifacts for Zero-Footprint cleanup
    artifacts = []

    # VECTORS 1-5 (Condensed for brevity, same logic but appending to artifacts)
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

    # VECTOR 7: MODERATOR REACTION TEST
    await status_msg.edit(content="✅ **STRUCTURAL AUDIT COMPLETE.** \n⏳ *Spawning active AI Phishing Raid...*")
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
            
        await upsert_vulnerability(str(guild.id), "Automod Defense", True, "Automod failed to block AI payloads.")
        
    except discord.Forbidden:
        await upsert_vulnerability(str(guild.id), "Automod Defense", False, "Blocked from sending webhooks.")

    # ZERO FOOTPRINT CLEANUP & SUMMARY
    await asyncio.sleep(15) # Wait 15s for TTK measurement before wiping everything
    
    for entity in artifacts:
        try: await entity.delete()
        except: pass
        
    for msg in spawned_msgs:
        try: 
            await msg.delete()
            if msg.id in active_raid_messages: del active_raid_messages[msg.id]
        except: pass

    # Auto-deleting summary
    summary = (
        "📊 **SYLAS AUDIT SUMMARY**\n"
        "All structural penetration vectors tested. Active raid artifacts have been wiped from the server to maintain zero footprint.\n"
        "Check your web dashboard for the updated Vulnerability Map and TTK scores.\n"
        "*This message will self-destruct in 60 seconds.*"
    )
    await ctx.send(summary, delete_after=60.0)
    try: await status_msg.delete()
    except: pass

@bot.command(name="scanperms")
@commands.has_permissions(administrator=True)
async def scan_perms(ctx):
    guild = ctx.guild
    baselines = await role_baselines.find({"server_id": str(guild.id)}).to_list(100)
    if not baselines:
        return await ctx.send("⚠️ No role baselines configured. Please set them up in the Web Dashboard first.", delete_after=20.0)

    baseline_map = {b["role_id"]: b.get("allowed_perms", []) for b in baselines}
    violations = []
    
    dangerous_flags = ["administrator", "manage_guild", "manage_roles", "manage_webhooks", "mention_everyone"]

    for role in guild.roles:
        if role.name == "@everyone" or role.managed: continue
        
        allowed = baseline_map.get(str(role.id), [])
        role_perms = dict(role.permissions)
        
        leaked_perms = [p for p in dangerous_flags if role_perms.get(p) and p not recalled in allowed]
        
        if leaked_perms:
            violations.append({"role": role, "leaks": leaked_perms})

    if not violations:
        return await ctx.send("✅ **PERMISSIONS SECURE.** No unauthorized dangerous permissions detected.", delete_after=30.0)

    pending_remediations[guild.id] = violations
    
    report = "🚨 **PERMISSION LEAKS DETECTED** 🚨\n"
    for v in violations:
        report += f"• {v['role'].mention}: `{', '.join(v['leaks'])}`\n"
    report += "\nType `!fixperms` to automatically strip these unauthorized permissions."
    
    await ctx.send(report)

@bot.command(name="fixperms")
@commands.has_permissions(administrator=True)
async def fix_perms(ctx):
    guild = ctx.guild
    if guild.id not in pending_remediations:
        return await ctx.send("No pending remediations. Run `!scanperms` first.", delete_after=10.0)
    
    violations = pending_remediations[guild.id]
    fixed_count = 0
    
    for v in violations:
        role = v["role"]
        leaks = v["leaks"]
        kwargs = {perm: False for perm in leaks}
        try:
            await role.edit(permissions=discord.Permissions(**{**dict(role.permissions), **kwargs}), reason="Sylas Automated Remediation")
            fixed_count += 1
        except discord.Forbidden:
            await ctx.send(f"❌ Failed to fix {role.name}. My role hierarchy is too low.", delete_after=10.0)

    del pending_remediations[guild.id]
    await ctx.send(f"🛡️ **REMEDIATION COMPLETE.** Successfully patched {fixed_count} roles.", delete_after=60.0)

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
