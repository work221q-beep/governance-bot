import os, asyncio, discord, random
from discord.ext import commands
from ai import get_preloaded_payloads
from db import payload_armory
from premium import is_guild_premium, check_cooldown, set_cooldown, PREMIUM_FEATURES

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

engine_state = {"active": True}
active_wargames = {}
pending_dropdowns = {} 
active_guild_sessions = set() 
startraid_abort_confirm = {} 

class PremiumUpgradeView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__()
        self.add_item(discord.ui.Button(label="Unlock Premium Protocols", url=f"{BASE_URL}/server/{guild_id}/premium", style=discord.ButtonStyle.link, emoji="💎"))

class RaidSelect(discord.ui.Select):
    def __init__(self, original_cmd_msg):
        self.original_cmd_msg = original_cmd_msg
        options = [
            discord.SelectOption(label="Phishing Link Wargame", description="Free | Detect obfuscated URLs.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Spam Flood Wargame", description="Free | Handle bot floods.", emoji="🌊", value="spam_flood"),
            discord.SelectOption(label="[💎 Premium] Fake Moderator", description="Verify authority.", emoji="🛡️", value="fake_mod"),
            discord.SelectOption(label="[💎 Premium] Insider Threat", description="Trusted users turning rogue.", emoji="🕵️", value="insider_threat"),
            discord.SelectOption(label="[💎 Premium] Escalation Conflict", description="Judge proportional response.", emoji="🤬", value="escalation"),
            discord.SelectOption(label="[💎 Premium] Coordinated Harass", description="Stop targeted brigading.", emoji="🎯", value="harassment")
        ]
        super().__init__(placeholder="Select a Training Protocol...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_raid = self.values[0]
        guild_id = interaction.guild.id
        
        is_prem = await is_guild_premium(guild_id)
        if selected_raid in PREMIUM_FEATURES and not is_prem:
            await interaction.response.send_message("🛑 **Premium Security Feature.**\nPlease purchase a license.", ephemeral=True, view=PremiumUpgradeView(guild_id))
            return 

        allowed, time_left = await check_cooldown(guild_id, selected_raid, is_prem)
        if not allowed:
            tier_text = "4-Hour" if is_prem else "24-Hour"
            await interaction.response.send_message(f"⏳ **Protocol on Cooldown.**\n**Time Remaining:** {time_left}", ephemeral=True)
            return

        pending_dropdowns.pop(interaction.message.id, None)
        await interaction.response.edit_message(content="⚡ **Deploying protocol...**", view=None) 
        await execute_wargame(interaction, selected_raid, interaction.message, self.original_cmd_msg)

class RaidView(discord.ui.View):
    def __init__(self, original_cmd_msg):
        super().__init__(timeout=60.0) 
        self.original_cmd_msg = original_cmd_msg
        self.message = None 
        self.add_item(RaidSelect(original_cmd_msg))

    async def on_timeout(self):
        if self.original_cmd_msg.guild.id in active_guild_sessions:
            active_guild_sessions.remove(self.original_cmd_msg.guild.id)
            
        if self.message and self.message.id in pending_dropdowns:
            pending_dropdowns.pop(self.message.id, None)
            try: await self.message.delete()
            except: pass
        try: await self.original_cmd_msg.delete()
        except: pass

@bot.event
async def on_ready():
    await bot.tree.sync() 
    print(f"👻 Sylas Ghost Engine is online.")

@bot.tree.command(name="startraid", description="Deploy a Red Team Wargame in this channel")
@discord.app_commands.default_permissions(administrator=True)
async def start_raid(interaction: discord.Interaction):
    if not engine_state["active"]:
        await interaction.response.send_message("❌ **Engine Offline.**", ephemeral=True)
        return

    if interaction.guild.id in active_guild_sessions:
        now = discord.utils.utcnow()
        last_attempt = startraid_abort_confirm.get(interaction.guild.id)
        
        if last_attempt and (now - last_attempt).total_seconds() < 15:
            startraid_abort_confirm.pop(interaction.guild.id, None)
            
            for game_id, wargame in list(active_wargames.items()):
                channel = bot.get_channel(wargame["channel_id"])
                if channel and channel.guild.id == interaction.guild.id: wargame["cancelled"] = True
                    
            to_remove = []
            for msg_id, msg in pending_dropdowns.items():
                if msg.guild and msg.guild.id == interaction.guild.id:
                    to_remove.append(msg_id)
                    try: await msg.delete()
                    except: pass
            for m_id in to_remove: pending_dropdowns.pop(m_id, None)
                
            if interaction.guild.id in active_guild_sessions: active_guild_sessions.remove(interaction.guild.id)
            await interaction.response.send_message("🛑 **Active wargame terminated.**", ephemeral=True, delete_after=10.0)
            return
        else:
            startraid_abort_confirm[interaction.guild.id] = now
            await interaction.response.send_message("⚠️ **A wargame is already active.** Type `/startraid` again to abort, or `/endraid`.", ephemeral=True, delete_after=15.0)
            return

    active_guild_sessions.add(interaction.guild.id)
    embed = discord.Embed(title="👻 SYLAS TRAINING ENGINE", description="**Select a Wargame to deploy.**\n\nDeployment drops an unknown mix of AI threats and contextual false positives.", color=discord.Color.dark_gray())
    
    await interaction.response.send_message(embed=embed)
    dropdown_msg = await interaction.original_response()
    
    view = RaidView(dropdown_msg)
    await dropdown_msg.edit(view=view)
    view.message = dropdown_msg
    pending_dropdowns[dropdown_msg.id] = dropdown_msg

@bot.tree.command(name="endraid", description="Forcefully terminate an active wargame")
@discord.app_commands.default_permissions(administrator=True)
async def end_raid(interaction: discord.Interaction):
    if interaction.guild.id not in active_guild_sessions:
        await interaction.response.send_message("⚠️ There is no active wargame in this server.", ephemeral=True)
        return

    killed_active_game = False
    for game_id, wargame in list(active_wargames.items()):
        channel = bot.get_channel(wargame["channel_id"])
        if channel and channel.guild.id == interaction.guild.id:
            wargame["cancelled"] = True
            killed_active_game = True
            
    to_remove = []
    for msg_id, msg in pending_dropdowns.items():
        if msg.guild.id == interaction.guild.id:
            to_remove.append(msg_id)
            try: await msg.delete()
            except: pass
    for m_id in to_remove: pending_dropdowns.pop(m_id, None)

    if interaction.guild.id in active_guild_sessions: active_guild_sessions.remove(interaction.guild.id)

    if killed_active_game: await interaction.response.send_message("🛑 **Active wargame terminated.**", ephemeral=True, delete_after=10.0)
    else: await interaction.response.send_message("🛑 **Deployment Cancelled.**", ephemeral=True)

async def execute_wargame(interaction: discord.Interaction, raid_type: str, dropdown_msg: discord.Message, original_cmd_msg: discord.Message):
    channel = interaction.channel
    status_embed = discord.Embed(title="⚡ Wargame Active", description="Injecting threats and contextual false positives...", color=discord.Color.dark_purple())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    game_id = str(interaction.id)
    
    scam_count = random.choice([2, 3])
    innocent_count = 5 - scam_count
    
    scams = await get_preloaded_payloads(scam_count, raid_type)
    innocents = await get_preloaded_payloads(innocent_count, f"innocent_{raid_type}")
    
    for s in scams: s["is_malicious"] = True
    for i in innocents: i["is_malicious"] = False
        
    used_ids = [doc["_id"] for doc in scams + innocents if doc.get("_id")]
    if used_ids: await payload_armory.delete_many({"_id": {"$in": used_ids}})
    
    all_payloads = scams + innocents
    random.shuffle(all_payloads)
    
    valid_names = [p["username"] for p in all_payloads if p.get("username")]
    wh_base_name = random.choice(valid_names)[:32] if valid_names else "Sylas_Ghost"
    
    try:
        try:
            existing_webhooks = await channel.webhooks()
            if len(existing_webhooks) >= 8:
                for wh in existing_webhooks:
                    if wh.name.startswith("Sylas"): 
                        try: await wh.delete()
                        except: pass
        except Exception: pass

        webhook = await channel.create_webhook(name=wh_base_name)
        artifacts.append(webhook)
        
        await status_msg.edit(embed=discord.Embed(title="⚔️ Wargame Deployed", description="Monitoring channel for Mod Response...", color=discord.Color.red()))
        
        active_wargames[game_id] = {
            "status_msg_id": status_msg.id, "dropdown_msg_id": dropdown_msg.id, 
            "channel_id": channel.id, "start_time": discord.utils.utcnow(),
            "scams_left": scam_count, "failed": False, "cancelled": False, "msg_map": {},
            "attempts": 0, "guild_id": interaction.guild.id, "raid_type": raid_type
        }
        
        for p in all_payloads:
            try:
                msg = await webhook.send(content=p["spam_message"], username=p["username"], wait=True)
                spawned_msgs.append(msg)
                active_wargames[game_id]["msg_map"][msg.id] = p["is_malicious"]
            except Exception: pass
            await asyncio.sleep(0.5)

        for _ in range(60):
            wargame = active_wargames.get(game_id)
            if not wargame or wargame["failed"] or wargame.get("cancelled") or wargame["scams_left"] <= 0: break
            await asyncio.sleep(1)

    except Exception as e:
        print(f"Wargame Execution Error: {e}")
    finally:
        if interaction.guild.id in active_guild_sessions:
            active_guild_sessions.remove(interaction.guild.id)
            
        wargame = active_wargames.get(game_id)
        
        if wargame and not wargame.get("cancelled"):
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

            try: 
                await status_msg.edit(embed=final_embed)
                await status_msg.delete(delay=15.0)
            except: pass
            
            if wargame.get("attempts", 0) > 0 or (wargame["scams_left"] == 0 and not wargame["failed"]):
                await set_cooldown(wargame["guild_id"], wargame["raid_type"])
                
        elif wargame and wargame.get("cancelled"):
            try: await status_msg.delete()
            except: pass

            if wargame.get("attempts", 0) > 0:
                await set_cooldown(wargame["guild_id"], wargame["raid_type"])
            if wargame.get("cancelled_reason") == "purge":
                try: await channel.send("🛑 **Wargame Cancelled.** Element purged by moderator.", delete_after=10.0)
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

# FIX: Added a robust background task wrapper to safely suppress discord.NotFound exceptions
@bot.event
async def on_message_delete(message):
    if message.id in pending_dropdowns:
        original_cmd_msg = pending_dropdowns.pop(message.id)
        if message.guild.id in active_guild_sessions:
            active_guild_sessions.remove(message.guild.id)
            
        async def safe_delete(msg):
            try:
                await msg.delete()
            except discord.NotFound:
                # The message was already deleted before the task could run.
                pass
            except Exception:
                pass
                
        bot.loop.create_task(safe_delete(original_cmd_msg))
        return

    for game_id, wargame in list(active_wargames.items()):
        if message.id in [wargame.get("status_msg_id"), wargame.get("dropdown_msg_id")]:
            if not wargame.get("cancelled"):
                wargame["cancelled"] = True
                wargame["cancelled_reason"] = "purge"
            continue

        if message.id in wargame["msg_map"]:
            wargame["attempts"] += 1
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
            except Exception: pass

async def start_bot():
    await bot.start(DISCORD_TOKEN)