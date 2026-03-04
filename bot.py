import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability, server_configs
from ai import generate_raid_payloads

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

active_raid_messages = {}

class RaidSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Phishing & Scam Flood", description="Deploys AI-generated malicious links and crypto scams.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Structural Nuke", description="Attempts to mass-create unauthorized roles, channels, and webhooks.", emoji="💥", value="nuke"),
            discord.SelectOption(label="Mass Ping Raid", description="Tests @everyone and @here mention vulnerabilities with urgent lures.", emoji="🔔", value="ping"),
            discord.SelectOption(label="Full Chaos Engine", description="Deploy the entire Red Team arsenal sequentially.", emoji="🔥", value="all")
        ]
        super().__init__(placeholder="Select a Penetration Testing Module...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Remove the dropdown menu once an option is selected to prevent double-clicks
        await interaction.response.edit_message(view=None) 
        await execute_raid(interaction, self.values[0])

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
    """Initiates the interactive Red Team Command Panel."""
    embed = discord.Embed(
        title="🛡️ SYLAS RED TEAM ENGINE",
        description="**Select a threat vector to simulate on this server.**\n\nAll tests are non-destructive and self-cleaning. Artifacts (spam, channels, roles) will be automatically scrubbed after 15 seconds to measure Time-To-Kill (TTK).",
        color=discord.Color.red()
    )
    embed.set_footer(text="Sylas Enterprise Suite • Administrator Only")
    await ctx.send(embed=embed, view=RaidView())

async def execute_raid(interaction: discord.Interaction, raid_type: str):
    guild = interaction.guild
    channel = interaction.channel
    config = await server_configs.find_one({"server_id": str(guild.id)}) or {"model": "llama3"}
    
    status_embed = discord.Embed(title="⚡ Initializing Chaos Cycle", color=discord.Color.orange())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    
    # 1. STRUCTURAL NUKE LOGIC
    if raid_type in ["nuke", "all"]:
        await status_msg.edit(embed=discord.Embed(title="💥 Vector: Structural Nuke", description="Attempting to bypass channel/role creation limits...", color=discord.Color.orange()))
        await asyncio.sleep(1)
        
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
            w = await channel.create_webhook(name="Sylas_Exploit")
            artifacts.append(w)
            await upsert_vulnerability(str(guild.id), "Webhook Exploitation", True, "Spawned webhook.")
        except discord.Forbidden: await upsert_vulnerability(str(guild.id), "Webhook Exploitation", False, "Blocked.")

    # 2. PHISHING / PING SPAM LOGIC
    if raid_type in ["phishing", "ping", "all"]:
        await status_msg.edit(embed=discord.Embed(title="🧠 Vector: AI Payload Generation", description="Compiling adversarial payloads... (Pinging AI Engine)", color=discord.Color.orange()))
        
        webhook = None
        try:
            webhook = await channel.create_webhook(name="Sylas_Scanner")
            artifacts.append(webhook)
            
            # Fetch payloads tailored to the specific raid type
            payloads = await generate_raid_payloads(3, raid_type, primary_model=config.get("model", "llama3"))
            
            await status_msg.edit(embed=discord.Embed(title="🎣 Vector: Active Payload Deployment", description="Bypassing Automod and deploying payloads via webhook...", color=discord.Color.orange()))
            
            for p in payloads:
                msg = await webhook.send(
                    content=p.get("spam_message", "HACKED"), username=p.get("username", "Ghost"), wait=True
                )
                spawned_msgs.append(msg)
                active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": channel.id}
                await asyncio.sleep(0.5)
                
            await upsert_vulnerability(str(guild.id), "Automod Defense", True, f"Automod failed to block {raid_type} payloads.")
            
        except discord.Forbidden:
            await upsert_vulnerability(str(guild.id), "Automod Defense", False, "Blocked from sending webhooks.")
        except Exception as e:
            print(f"Raid execution error: {e}")

    # ZERO FOOTPRINT PROTOCOL
    await status_msg.edit(embed=discord.Embed(title="⏳ Tracking Time-To-Kill (TTK)", description="Monitoring moderator and automod response for 15 seconds before auto-scrubbing artifacts...", color=discord.Color.yellow()))
    await asyncio.sleep(15) 
    
    for entity in artifacts:
        try: await entity.delete()
        except: pass
        
    for msg in spawned_msgs:
        try: 
            await msg.delete()
            if msg.id in active_raid_messages: del active_raid_messages[msg.id]
        except: pass

    await status_msg.edit(embed=discord.Embed(
        title="✅ AUDIT COMPLETE", 
        description="All requested vectors tested. Raid artifacts have been wiped to maintain a zero footprint.\n\n**Check your Web Dashboard for the updated Threat Map.**",
        color=discord.Color.green()
    ), delete_after=30.0)

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
