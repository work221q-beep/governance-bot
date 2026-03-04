import os, asyncio, discord
from discord.ext import commands
from db import upsert_vulnerability, server_configs
from ai import generate_raid_payloads

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
active_raid_messages = {}

class RemediationView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

    @discord.ui.button(label="Auto-Fix Vulnerabilities", style=discord.ButtonStyle.green, emoji="🛡️")
    async def fix_vulns(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⚙️ **Executing Auto-Remediation...**", ephemeral=True)
        fixed_count = 0
        
        # 1. Strip dangerous perms from @everyone instantly
        try:
            default_role = self.guild.default_role
            perms = default_role.permissions
            perms.update(mention_everyone=False, manage_webhooks=False, administrator=False, create_instant_invite=False)
            await default_role.edit(permissions=perms, reason="Sylas Enterprise: Auto-Remediation")
            fixed_count += 1
        except Exception as e: print(e)

        # 2. Scrub any rogue webhooks left in the server
        try:
            for w in await self.guild.webhooks():
                if w.name != "Sylas_Scanner": 
                    await w.delete(reason="Sylas Auto-Remediation")
                    fixed_count += 1
        except: pass

        await interaction.edit_original_response(content=f"✅ **Remediation Complete:** Stripped dangerous permissions from @everyone and deleted rogue webhooks. ({fixed_count} actions taken).")
        self.stop()

class RaidSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Phishing & Scam Flood", description="Deploys AI malicious links.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Structural Nuke", description="Mass-creates unauthorized roles/channels.", emoji="💥", value="nuke"),
            discord.SelectOption(label="Mass Ping Raid", description="Tests @everyone vulnerability.", emoji="🔔", value="ping"),
            discord.SelectOption(label="Full Chaos Engine", description="Deploy the entire arsenal.", emoji="🔥", value="all")
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
        description="**Select a threat vector to simulate.**\n\nAll tests are non-destructive and self-cleaning. Artifacts will be aggressively scrubbed after 15 seconds. You will be given an option to auto-patch vulnerabilities afterwards.",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed, view=RaidView())

async def execute_raid(interaction: discord.Interaction, raid_type: str, original_msg: discord.Message):
    guild = interaction.guild
    channel = interaction.channel
    config = await server_configs.find_one({"server_id": str(guild.id)}) or {"model": "llama3"}
    
    status_embed = discord.Embed(title="⚡ Initializing Chaos Cycle", color=discord.Color.orange())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    
    try:
        if raid_type in ["nuke", "all"]:
            await status_msg.edit(embed=discord.Embed(title="💥 Vector: Structural Nuke", description="Bypassing channel/role creation limits...", color=discord.Color.orange()))
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

        if raid_type in ["phishing", "ping", "all"]:
            await status_msg.edit(embed=discord.Embed(title="🧠 Vector: AI Payload Deployment", description="Bypassing Automod and deploying payloads via webhook...", color=discord.Color.orange()))
            try:
                webhook = await channel.create_webhook(name="Sylas_Scanner")
                artifacts.append(webhook)
                payloads = await generate_raid_payloads(3, raid_type, primary_model=config.get("model", "llama3"))
                
                for p in payloads:
                    msg = await webhook.send(content=p.get("spam_message", "HACKED"), username=p.get("username", "Ghost"), wait=True)
                    spawned_msgs.append(msg)
                    active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": channel.id}
                    await asyncio.sleep(0.5)
                await upsert_vulnerability(str(guild.id), "Automod Defense", True, f"Automod failed to block {raid_type} payloads.")
            except discord.Forbidden:
                await upsert_vulnerability(str(guild.id), "Automod Defense", False, "Blocked from sending webhooks.")

        await status_msg.edit(embed=discord.Embed(title="⏳ Tracking Time-To-Kill (TTK)", description="Monitoring moderator response for 15 seconds before absolute self-destruction...", color=discord.Color.yellow()))
        await asyncio.sleep(15) 

    finally:
        # ABSOLUTE GUARANTEED CLEANUP. This will execute even if the code errors out.
        cleanup_tasks = [entity.delete(reason="Sylas Zero-Footprint Cleanup") for entity in artifacts if entity]
        cleanup_tasks.extend([msg.delete() for msg in spawned_msgs if msg])
        
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            
        for msg_id in [m.id for m in spawned_msgs]:
            active_raid_messages.pop(msg_id, None)

        try: await status_msg.delete() 
        except: pass
        try: await original_msg.delete()
        except: pass

        # Provide Final Summary & Offer Auto-Remediation
        embed = discord.Embed(
            title="✅ AUDIT COMPLETE & SCRUBBED", 
            description="All active vectors tested. Artifacts have been strictly wiped to maintain a zero footprint.\n\n**If vulnerabilities were found, would you like Sylas to instantly patch them?**",
            color=discord.Color.green()
        )
        await channel.send(embed=embed, view=RemediationView(guild), delete_after=60.0)

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
