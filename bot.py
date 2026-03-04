import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability, server_configs
from ai import get_preloaded_payloads

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
active_raid_messages = {}

class RaidSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Phishing & Scam Flood", description="Deploys payloads instantly from the AI Armory.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Structural Nuke", description="Tests unauthorized role/channel/webhook creation.", emoji="💥", value="nuke"),
            discord.SelectOption(label="Verification Gate Audit", description="Scans @everyone restrictions.", emoji="🛡️", value="gate"),
            discord.SelectOption(label="Full Chaos Engine", description="Deploy the entire testing suite.", emoji="🔥", value="all")
        ]
        super().__init__(placeholder="Select a Penetration Testing Module...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="*Executing Protocol...*", view=None) 
        await execute_raid(interaction, self.values[0], interaction.message)

class RaidView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(RaidSelect())

@bot.event
async def on_ready():
    print(f"🔥 Sylas Enterprise Core is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx):
    embed = discord.Embed(
        title="🛡️ SYLAS RED TEAM ENGINE",
        description="**Select a threat vector to simulate.**\n\nAll destructive tests are self-cleaning. Artifacts will be aggressively scrubbed after 15 seconds. Permissions should be managed exclusively via the Web Dashboard.",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed, view=RaidView())

async def execute_raid(interaction: discord.Interaction, raid_type: str, original_msg: discord.Message):
    guild = interaction.guild
    channel = interaction.channel
    
    status_embed = discord.Embed(title="⚡ Initializing Chaos Cycle", color=discord.Color.orange())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    
    try:
        if raid_type in ["gate", "all"]:
            await status_msg.edit(embed=discord.Embed(title="🛡️ Vector: Verification Gate Audit", description="Analyzing base channel overrides and @everyone restrictions...", color=discord.Color.blue()))
            await asyncio.sleep(2)
            everyone_role = guild.default_role
            if everyone_role.permissions.send_messages or everyone_role.permissions.create_instant_invite:
                await upsert_vulnerability(str(guild.id), "Verification Gate Bypass", True, "The @everyone role has base permissions to send messages or create invites.")
            else:
                await upsert_vulnerability(str(guild.id), "Verification Gate Bypass", False, "Global @everyone permissions are properly restricted.")

        if raid_type in ["nuke", "all"]:
            await status_msg.edit(embed=discord.Embed(title="💥 Vector: Structural Nuke", description="Bypassing channel/role creation limits...", color=discord.Color.orange()))
            try:
                c = await guild.create_text_channel("sylas-audit-fail")
                artifacts.append(c)
                await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", True, "Bypassed restrictions.")
            except discord.Forbidden: await upsert_vulnerability(str(guild.id), "Channel Creation Bypass", False, "Blocked.")

            try:
                w = await channel.create_webhook(name="Sylas_Exploit")
                artifacts.append(w)
                await upsert_vulnerability(str(guild.id), "Webhook Exploitation", True, "Spawned unauthorized webhook.")
            except discord.Forbidden: await upsert_vulnerability(str(guild.id), "Webhook Exploitation", False, "Blocked.")

        if raid_type in ["phishing", "all"] or raid_type == "ping":
            fetch_type = "ping" if raid_type == "ping" else "phishing"
            await status_msg.edit(embed=discord.Embed(title="🧠 Vector: Armory Payload Extraction", description="Pulling pre-generated AI payloads from the MongoDB Armory...", color=discord.Color.purple()))
            
            try:
                webhook = await channel.create_webhook(name="Sylas_Scanner")
                artifacts.append(webhook)
                
                # Lightning fast 0ms database pull
                payloads = await get_preloaded_payloads(3, fetch_type)
                
                await status_msg.edit(embed=discord.Embed(title="🎣 Vector: Payload Deployment", description="Deploying AI-generated payloads via webhook...", color=discord.Color.red()))
                
                for p in payloads:
                    msg = await webhook.send(content=p.get("spam_message", "HACKED"), username=p.get("username", "Ghost"), wait=True)
                    spawned_msgs.append(msg)
                    active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": channel.id}
                    await asyncio.sleep(0.5)
                    
                await upsert_vulnerability(str(guild.id), "Automod Defense", True, f"Automod failed to block payloads.")
            except discord.Forbidden:
                await upsert_vulnerability(str(guild.id), "Automod Defense", False, "Blocked from sending webhooks.")

        if spawned_msgs:
            await status_msg.edit(embed=discord.Embed(title="⏳ Tracking Time-To-Kill (TTK)", description="Monitoring moderator response for 15 seconds before absolute self-destruction...", color=discord.Color.yellow()))
            await asyncio.sleep(15) 

    finally:
        cleanup_tasks = [entity.delete(reason="Sylas Zero-Footprint Cleanup") for entity in artifacts if entity]
        cleanup_tasks.extend([msg.delete() for msg in spawned_msgs if msg])
        if cleanup_tasks: await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        for msg_id in [m.id for m in spawned_msgs]: active_raid_messages.pop(msg_id, None)

        try: await status_msg.delete() 
        except: pass
        try: await original_msg.delete()
        except: pass

        embed = discord.Embed(
            title="✅ AUDIT COMPLETE & SCRUBBED", 
            description="All active vectors tested. Artifacts have been strictly wiped to maintain a zero footprint.\n\n**Please visit the Sylas Web Dashboard to review the Threat Map and manage role permissions.**",
            color=discord.Color.green()
        )
        await channel.send(embed=embed, delete_after=60.0)

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
