import os, asyncio, discord, random
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
            discord.SelectOption(label="Phishing Link Wargame", description="Detect obfuscated URLs vs safe links.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Spam Flood Wargame", description="Handle bot floods vs excited users.", emoji="🌊", value="spam_flood"),
            discord.SelectOption(label="Fake Moderator Wargame", description="Verify authority & social engineering.", emoji="🛡️", value="fake_mod"),
            discord.SelectOption(label="Insider Threat Wargame", description="Trusted users turning rogue.", emoji="🕵️", value="insider_threat"),
            discord.SelectOption(label="Escalation Conflict", description="Judge proportional response in arguments.", emoji="🤬", value="escalation"),
            discord.SelectOption(label="Coordinated Harassment", description="Identify and stop targeted brigading.", emoji="🎯", value="harassment")
        ]
        super().__init__(placeholder="Select a Training Protocol...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        pending_dropdowns.pop(interaction.message.id, None)
        await interaction.response.edit_message(view=None) 
        await execute_wargame(interaction, self.values[0], interaction.message, self.original_cmd_msg)

class RaidView(discord.ui.View):
    def __init__(self, original_cmd_msg):
        super().__init__(timeout=60.0) 
        self.original_cmd_msg = original_cmd_msg
        self.message = None 
        self.add_item(RaidSelect(original_cmd_msg))

    async def on_timeout(self):
        if self.message and self.message.id in pending_dropdowns:
            pending_dropdowns.pop(self.message.id, None)
            try: await self.message.delete()
            except: pass
        try: await self.original_cmd_msg.delete()
        except: pass

@bot.event
async def on_ready():
    print(f"👻 Sylas Ghost Engine is online.")

@bot.command(name="startraid")
@commands.has_permissions(administrator=True)
async def start_raid(ctx):
    if not engine_state["active"]:
        msg = await ctx.send("❌ **Engine Offline.** Contact the Bot Administrator.", delete_after=5.0)
        await asyncio.sleep(5)
        try: await ctx.message.delete()
        except: pass
        return

    embed = discord.Embed(
        title="👻 SYLAS TRAINING ENGINE",
        description="**Select a Wargame to deploy.**\n\nDeployment drops 3 AI threats and 2 contextual false positives. Mods have **60 seconds** to delete the threats. Deleting an innocent message results in immediate failure.",
        color=discord.Color.dark_gray()
    )
    
    view = RaidView(ctx.message)
    dropdown_msg = await ctx.send(embed=embed, view=view)
    view.message = dropdown_msg
    pending_dropdowns[dropdown_msg.id] = ctx.message

async def execute_wargame(interaction: discord.Interaction, raid_type: str, dropdown_msg: discord.Message, original_cmd_msg: discord.Message):
    channel = interaction.channel
    status_embed = discord.Embed(title="⚡ Wargame Active", description="Injecting threats and contextual false positives...", color=discord.Color.dark_purple())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    game_id = str(interaction.id)
    
    # Generate Payload Mix via Database (Pulls contextual innocents!)
    scams = await get_preloaded_payloads(3, raid_type)
    innocents = await get_preloaded_payloads(2, f"innocent_{raid_type}")
    
    for s in scams: s["is_malicious"] = True
    for i in innocents: i["is_malicious"] = False
        
    # 🔥 BURN AFTER READING PROTOCOL
    used_ids = [doc["_id"] for doc in scams + innocents if doc.get("_id")]
    if used_ids: await payload_armory.delete_many({"_id": {"$in": used_ids}})
    
    all_payloads = scams + innocents
    random.shuffle(all_payloads)
    
    try:
        webhook = await channel.create_webhook(name="Sylas_Ghost")
        artifacts.append(webhook)
        
        await status_msg.edit(embed=discord.Embed(title="⚔️ Wargame Deployed", description="Monitoring channel for Mod Response...", color=discord.Color.red()))
        
        active_wargames[game_id] = {
            "status_msg_id": status_msg.id, "channel_id": channel.id, "start_time": discord.utils.utcnow(),
            "scams_left": 3, "failed": False, "msg_map": {}
        }
        
        for p in all_payloads:
            msg = await webhook.send(content=p["spam_message"], username=p["username"], wait=True)
            spawned_msgs.append(msg)
            active_wargames[game_id]["msg_map"][msg.id] = p["is_malicious"]
            await asyncio.sleep(0.5)

        for _ in range(60):
            wargame = active_wargames.get(game_id)
            if not wargame or wargame["failed"] or wargame["scams_left"] <= 0: break
            await asyncio.sleep(1)

    finally:
        wargame = active_wargames.get(game_id)
        if wargame:
            final_embed = discord.Embed(title="✅ WARGAME COMPLETE")
            if wargame["failed"]:
                final_embed.title = "❌ WARGAME FAILED"
                final_embed.description = "A moderator deleted an innocent message. Structural integrity compromised."
                final_embed.color = discord.Color.red()
            elif wargame["scams_left"] > 0:
                final_embed.title = "❌ WARGAME FAILED (TIMEOUT)"
                final_embed.description = f"Mods failed to delete {wargame['scams_left']} threats within 60 seconds."
                final_embed.color = discord.Color.orange()
            else:
                final_embed.description = "All threats neutralized successfully without casualties. Perfect execution."
                final_embed.color = discord.Color.green()

            try: await status_msg.edit(embed=final_embed, delete_after=15.0)
            except: pass
            active_wargames.pop(game_id, None)

        for msg in spawned_msgs:
            try: await msg.delete()
            except: pass
        for entity in artifacts:
            try: await entity.delete()
            except: pass
        try: await dropdown_msg.delete()
        except: pass
        try: await original_cmd_msg.delete()
        except: pass

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
                    embed.add_field(name="🚨 FATAL ERROR", value=f"Mod deleted a contextual false positive at **{time_alive:.1f}s**!", inline=False)
                else:
                    wargame["scams_left"] -= 1
                    embed.add_field(name="🛡️ Threat Neutralized", value=f"Payload deleted in **{time_alive:.1f}s**.", inline=False)
                
                await status_msg.edit(embed=embed)
            except Exception as e: pass

async def start_bot():
    await bot.start(DISCORD_TOKEN)
