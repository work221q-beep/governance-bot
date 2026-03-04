import os, asyncio, discord
from discord.ext import commands
from ai import get_preloaded_payloads

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
active_raid_messages = {}

class RaidSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Phishing Scam (Ghost Deployment)", description="Silently drops phishing payloads to test Mod TTK.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Mass Ping Raid (Ghost Deployment)", description="Silently drops urgent pings to test Mod TTK.", emoji="🔔", value="ping")
        ]
        super().__init__(placeholder="Select a Ghost Protocol...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=None) 
        await execute_raid(interaction, self.values[0], interaction.message)

class RaidView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(RaidSelect())

@bot.event
async def on_ready():
    print(f"👻 Sylas Ghost Engine is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx):
    embed = discord.Embed(
        title="👻 SYLAS GHOST ENGINE",
        description="**Select a payload to deploy.**\n\nDeployment is entirely silent. Moderator reaction times (TTK) will be updated directly on this message. All artifacts self-destruct rapidly.",
        color=discord.Color.dark_gray()
    )
    await ctx.send(embed=embed, view=RaidView())

async def execute_raid(interaction: discord.Interaction, raid_type: str, original_msg: discord.Message):
    channel = interaction.channel
    
    status_embed = discord.Embed(title="⚡ Ghost Deployment Active", description="Extracting payloads from MongoDB Armory...", color=discord.Color.dark_purple())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    
    try:
        webhook = await channel.create_webhook(name="System")
        artifacts.append(webhook)
        
        payloads = await get_preloaded_payloads(3, raid_type)
        
        await status_msg.edit(embed=discord.Embed(title="🎣 Payloads Deployed", description="Monitoring channel for moderator deletion...", color=discord.Color.red()))
        
        for p in payloads:
            msg = await webhook.send(content=p.get("spam_message", "HACKED"), username=p.get("username", "Ghost"), wait=True)
            spawned_msgs.append(msg)
            # Store the status_msg ID so we can edit it later when a mod deletes the spam
            active_raid_messages[msg.id] = {"time": discord.utils.utcnow(), "channel_id": channel.id, "status_msg_id": status_msg.id}
            await asyncio.sleep(0.5)

        # Wait 10 seconds (reduced from 15) before automatic cleanup if mods fail to react
        await asyncio.sleep(10) 

    finally:
        # Guaranteed Ghost Cleanup
        cleanup_tasks = [entity.delete(reason="Sylas Ghost Cleanup") for entity in artifacts if entity]
        cleanup_tasks.extend([msg.delete() for msg in spawned_msgs if msg])
        if cleanup_tasks: await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        for msg_id in [m.id for m in spawned_msgs]: active_raid_messages.pop(msg_id, None)

        # Append final scrubbed status to the message, delete rapidly after 10s
        final_embed = status_msg.embeds[0] if status_msg.embeds else discord.Embed(title="✅ GHOST AUDIT COMPLETE")
        final_embed.description = "All artifacts have been strictly wiped to maintain a zero footprint."
        final_embed.color = discord.Color.green()
        
        try: 
            await status_msg.edit(embed=final_embed, delete_after=10.0)
            await original_msg.delete()
        except: pass

@bot.event
async def on_message_delete(message):
    if message.id in active_raid_messages:
        raid_data = active_raid_messages[message.id]
        time_alive = (discord.utils.utcnow() - raid_data["time"]).total_seconds()
        
        try:
            channel = bot.get_channel(raid_data["channel_id"])
            status_msg = await channel.fetch_message(raid_data["status_msg_id"])
            
            # ⚡ INSTEAD OF SENDING A NEW MESSAGE, WE EDIT THE EXISTING ONE
            embed = status_msg.embeds[0]
            embed.add_field(name="🛡️ Threat Neutralized", value=f"Moderator deleted spam in **{time_alive:.1f} seconds**.", inline=False)
            embed.color = discord.Color.green()
            await status_msg.edit(embed=embed)
        except Exception as e:
            pass
            
        del active_raid_messages[message.id]

async def start_bot():
    await bot.start(DISCORD_TOKEN)
