import os, asyncio, discord, random, uuid
from discord.ext import commands
from ai import get_preloaded_payloads
from db import payload_armory

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

engine_state = {"active": True}
active_wargames = {}
pending_dropdowns = {} 

class RaidSelect(discord.ui.Select):
    def __init__(self, original_cmd_msg):
        self.original_cmd_msg = original_cmd_msg
        options = [
            discord.SelectOption(label="Phishing Scam Wargame", description="Drops scams + false positives.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Malicious Ping Wargame", description="Drops fake @everyone alerts.", emoji="🚨", value="ping")
        ]
        super().__init__(placeholder="Select the type of Red Team attack...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not engine_state["active"]:
            await interaction.response.send_message("⛔ The Red Team Engine is currently offline.", ephemeral=True)
            return

        raid_type = self.values[0]
        scam_count = 3
        innocent_count = 5 

        await interaction.response.send_message(f"🔥 Initializing **{raid_type.upper()}** wargame. Preparing payloads...", ephemeral=True)
        
        try:
            await self.original_cmd_msg.delete()
        except:
            pass
        if self.original_cmd_msg.id in pending_dropdowns:
            del pending_dropdowns[self.original_cmd_msg.id]

        channel = interaction.channel
        
        # 1. Fetch AI-generated Malicious Payloads
        scams = await get_preloaded_payloads(intensity=scam_count, raid_type=raid_type)
        
        # 2. Fetch AI-generated Innocent Payloads
        innocents = await get_preloaded_payloads(intensity=innocent_count, raid_type="innocent")

        game_id = str(uuid.uuid4())
        
        embed = discord.Embed(
            title="🎯 SYLAS RED TEAM: ACTIVE WARGAME",
            description=(
                f"**Type:** {raid_type.upper()}\n"
                f"**Threats:** {scam_count}\n"
                f"**Innocent Chatter:** {innocent_count}\n\n"
                "Moderators: Delete the malicious messages ASAP. Do NOT delete innocent messages."
            ),
            color=discord.Color.orange()
        )
        status_msg = await channel.send(embed=embed)

        active_wargames[game_id] = {
            "channel_id": channel.id,
            "status_msg_id": status_msg.id,
            "start_time": discord.utils.utcnow(),
            "scams_left": scam_count,
            "failed": False,
            "msg_map": {} 
        }

        # Sequence of booleans representing malicious (True) and innocent (False) messages
        sequence = [True] * scam_count + [False] * innocent_count
        random.shuffle(sequence)

        for is_malicious in sequence:
            if active_wargames[game_id]["failed"]:
                break
                
            if is_malicious:
                payload = scams.pop(0) if scams else {"username": "Ghost", "spam_message": "🚨 Fake Malicious Payload 🚨"}
            else:
                payload = innocents.pop(0) if innocents else {"username": "Gamer", "spam_message": "Just playing some games."}
                
            webhook = None
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.name == "SylasEngine":
                    webhook = wh
                    break
            if not webhook:
                webhook = await channel.create_webhook(name="SylasEngine")

            try:
                sent_msg = await webhook.send(
                    content=payload["spam_message"],
                    username=payload["username"],
                    avatar_url="https://cdn.discordapp.com/embed/avatars/0.png",
                    wait=True
                )
                active_wargames[game_id]["msg_map"][sent_msg.id] = is_malicious
            except Exception as e:
                print(f"Webhook error: {e}")

            await asyncio.sleep(random.uniform(1.5, 4.0))

        if not active_wargames[game_id]["failed"]:
            await asyncio.sleep(2) 
            try:
                final_status = await channel.fetch_message(status_msg.id)
                final_embed = final_status.embeds[0]
                
                if active_wargames[game_id]["scams_left"] > 0:
                    final_embed.color = discord.Color.red()
                    final_embed.add_field(name="⚠️ WARGAME FAILED", value=f"Moderators failed to delete {active_wargames[game_id]['scams_left']} threat(s).", inline=False)
                else:
                    final_embed.color = discord.Color.green()
                    final_embed.add_field(name="✅ WARGAME CLEARED", value="All threats neutralized cleanly.", inline=False)
                    
                await final_status.edit(embed=final_embed)
            except:
                pass

        del active_wargames[game_id]

class RaidView(discord.ui.View):
    def __init__(self, original_cmd_msg):
        super().__init__(timeout=60)
        self.add_item(RaidSelect(original_cmd_msg))

@bot.command()
@commands.has_permissions(administrator=True)
async def raidserver(ctx):
    if not engine_state["active"]:
        await ctx.send("⛔ The Red Team Engine is currently offline. Enable it from the Master Admin Panel.")
        return

    msg = await ctx.send("⚙️ **SYLAS RED TEAM**\nSelect a wargame to deploy in this channel:")
    view = RaidView(msg)
    pending_dropdowns[msg.id] = msg
    await msg.edit(view=view)

@bot.event
async def on_message_delete(message):
    if message.id in pending_dropdowns:
        original_cmd_msg = pending_dropdowns.pop(message.id)
        try:
            await message.channel.send("🛑 **Wargame Cancelled.** Dropdown menu was deleted by a moderator.", delete_after=5.0)
            await original_cmd_msg.delete()
        except: pass
        return

    for game_id, wargame in list(active_wargames.items()):
        if message.id in wargame["msg_map"]:
            is_malicious = wargame["msg_map"][message.id]
            time_alive = (discord.utils.utcnow() - wargame["start_time"]).total_seconds()
            
            try:
                channel = bot.get_channel(wargame["channel_id"])
                status_msg = await channel.fetch_message(wargame["status_msg_id"])
                embed = status_msg.embeds[0]
                
                if not is_malicious:
                    wargame["failed"] = True
                    embed.color = discord.Color.red()
                    embed.add_field(name="🚨 FATAL ERROR", value=f"Mod deleted an innocent message at **{time_alive:.1f}s**!", inline=False)
                else:
                    wargame["scams_left"] -= 1
                    embed.add_field(name="🛡️ Threat Neutralized", value=f"Scam deleted in **{time_alive:.1f}s**.", inline=False)
                
                await status_msg.edit(embed=embed)
            except:
                pass

async def start_bot():
    await bot.start(DISCORD_TOKEN)
