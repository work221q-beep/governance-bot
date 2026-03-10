import os, asyncio, discord, random, datetime
from discord.ext import commands
from ai import get_preloaded_payloads
from db import payload_armory
from premium import is_guild_premium, check_cooldown, set_cooldown, PREMIUM_FEATURES

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# --- SYSTEM MATRICES ---
engine_state = {"active": True}
active_wargames = {}

# --- PROTOCOL METADATA MAPPING ---
RAID_LABELS = {
    "phishing": "Phishing Link",
    "spam_flood": "Spam Flood",
    "fake_mod": "Fake Moderator",
    "insider_threat": "Insider Threat",
    "escalation": "Escalation Conflict",
    "harassment": "Coordinated Harassment"
}

# --- ANTI-SPAM & SESSION TRACKING ---
pending_dropdowns = {} 
active_guild_sessions = set() 
startraid_abort_confirm = {} 
post_raid_cooldowns = {} 

# --- PURGE DEBOUNCE TRACKER ---
channel_delete_activity = {}

async def get_menu_cooldown(guild_id: int) -> int:
    """Calculates the anti-spam menu cooldown based on premium tier."""
    from db import guild_premium
    prem_doc = await guild_premium.find_one({"guild_id": str(guild_id)})
    now = datetime.datetime.utcnow()
    
    if prem_doc and "expires_at" in prem_doc:
        exp = prem_doc["expires_at"]
        if isinstance(exp, str):
            try: exp = datetime.datetime.fromisoformat(exp.replace("Z", "+00:00"))
            except: exp = now - datetime.timedelta(days=1)
        if exp.tzinfo: exp = exp.replace(tzinfo=None)
        
        if exp > now:
            updated = prem_doc.get("updated_at", now)
            if isinstance(updated, str):
                try: updated = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
                except: updated = now
            if updated.tzinfo: updated = updated.replace(tzinfo=None)
            
            if (exp - updated).days >= 360:
                return 0
            return 15
    return 60

async def send_delayed_notice(channel_id: int, content: str = None, embed: discord.Embed = None):
    """
    Adaptive Debounce Task: Waits for a channel to experience 2.5 seconds of absolute silence 
    (meaning external purges are finished) before broadcasting critical notices.
    """
    start_time = asyncio.get_event_loop().time()
    
    while True:
        now = asyncio.get_event_loop().time()
        
        # Hard fallback: Prevent infinite loops in wildly active channels
        if now - start_time > 60.0:
            break
            
        last_activity = channel_delete_activity.get(channel_id, 0)
        elapsed = now - last_activity
        
        # If no messages have been deleted in the last 2.5 seconds, the purge is over.
        if elapsed >= 2.5:
            break
            
        await asyncio.sleep(0.5)
        
    channel = bot.get_channel(channel_id)
    if channel:
        try:
            if content and embed:
                await channel.send(content=content, embed=embed, delete_after=15.0)
            elif content:
                await channel.send(content=content, delete_after=15.0)
            elif embed:
                await channel.send(embed=embed, delete_after=15.0)
        except Exception:
            pass

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
        if interaction.message.id not in pending_dropdowns:
            await interaction.response.send_message("🛑 **Expired Menu.** This session was terminated or timed out.", ephemeral=True)
            try: await interaction.message.delete()
            except: pass
            return

        selected_raid = self.values[0]
        guild_id = interaction.guild.id
        
        is_prem = await is_guild_premium(guild_id)
        if selected_raid in PREMIUM_FEATURES and not is_prem:
            await interaction.response.send_message("🛑 **Premium Security Feature.**\nPlease purchase a license.", ephemeral=True, view=PremiumUpgradeView(guild_id))
            return 

        allowed, time_left = await check_cooldown(guild_id, selected_raid, is_prem)
        if not allowed:
            await interaction.response.send_message(f"⏳ **Protocol on Cooldown.**\n**Time Remaining:** {time_left}", ephemeral=True)
            return

        await set_cooldown(guild_id, selected_raid)

        pending_dropdowns.pop(interaction.message.id, None)
        await interaction.response.edit_message(content=f"⚡ **Deploying {RAID_LABELS.get(selected_raid, 'protocol')}...**", view=None) 
        await execute_wargame(interaction, selected_raid, interaction.message, self.original_cmd_msg)

class RaidView(discord.ui.View):
    def __init__(self, original_cmd_msg):
        super().__init__(timeout=60.0) 
        self.original_cmd_msg = original_cmd_msg
        self.message = None 
        self.add_item(RaidSelect(original_cmd_msg))

    async def on_timeout(self):
        guild_id = self.original_cmd_msg.guild.id
        if guild_id in active_guild_sessions:
            active_guild_sessions.remove(guild_id)
            post_raid_cooldowns[guild_id] = datetime.datetime.utcnow()
            
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

    now = datetime.datetime.utcnow()
    guild_id = interaction.guild.id

    if guild_id in active_guild_sessions:
        last_attempt = startraid_abort_confirm.get(guild_id)
        
        if last_attempt and (now - last_attempt).total_seconds() < 15:
            startraid_abort_confirm.pop(guild_id, None)
            
            for game_id, wargame in list(active_wargames.items()):
                channel = bot.get_channel(wargame["channel_id"])
                if channel and channel.guild.id == guild_id: 
                    wargame["cancelled"] = True
                    
            to_remove = []
            for msg_id, meta in pending_dropdowns.items():
                if meta["guild_id"] == guild_id:
                    to_remove.append(msg_id)
                    try: await meta["message"].delete()
                    except: pass
            for m_id in to_remove: 
                pending_dropdowns.pop(m_id, None)
                
            active_guild_sessions.discard(guild_id)
            post_raid_cooldowns[guild_id] = now
            
            await interaction.response.send_message("🛑 **Active wargame terminated.**", ephemeral=True, delete_after=10.0)
            return
            
        else:
            startraid_abort_confirm[guild_id] = now
            await interaction.response.send_message("⚠️ **A wargame is already active.** Type `/startraid` again to abort, or `/endraid`.", ephemeral=True, delete_after=15.0)
            return

    last_ended = post_raid_cooldowns.get(guild_id)
    if last_ended:
        cooldown_seconds = await get_menu_cooldown(guild_id)
        elapsed = (now - last_ended).total_seconds()
        
        if elapsed < cooldown_seconds:
            remaining = int(cooldown_seconds - elapsed)
            await interaction.response.send_message(f"⏳ **Engine Cooling Down.**\nPlease wait {remaining} seconds before initiating another deployment.", ephemeral=True)
            return

    active_guild_sessions.add(guild_id)
    embed = discord.Embed(title="👻 SYLAS TRAINING ENGINE", description="**Select a Wargame to deploy.**\n\nDeployment drops an unknown mix of AI threats and contextual false positives.", color=discord.Color.dark_gray())
    
    await interaction.response.send_message(embed=embed)
    dropdown_msg = await interaction.original_response()
    
    view = RaidView(dropdown_msg)
    await dropdown_msg.edit(view=view)
    view.message = dropdown_msg
    
    pending_dropdowns[dropdown_msg.id] = {
        "guild_id": guild_id,
        "channel_id": interaction.channel_id,
        "message": dropdown_msg
    }

@bot.tree.command(name="endraid", description="Forcefully terminate an active wargame")
@discord.app_commands.default_permissions(administrator=True)
async def end_raid(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in active_guild_sessions:
        await interaction.response.send_message("⚠️ There is no active wargame in this server.", ephemeral=True)
        return

    killed_active_game = False
    for game_id, wargame in list(active_wargames.items()):
        channel = bot.get_channel(wargame["channel_id"])
        if channel and channel.guild.id == guild_id:
            wargame["cancelled"] = True
            killed_active_game = True
            
    to_remove = []
    for msg_id, meta in pending_dropdowns.items():
        if meta["guild_id"] == guild_id:
            to_remove.append(msg_id)
            try: await meta["message"].delete()
            except: pass
    for m_id in to_remove: 
        pending_dropdowns.pop(m_id, None)

    active_guild_sessions.discard(guild_id)
    post_raid_cooldowns[guild_id] = datetime.datetime.utcnow()

    if killed_active_game: 
        await interaction.response.send_message("🛑 **Active wargame terminated.**", ephemeral=True, delete_after=10.0)
    else: 
        await interaction.response.send_message("🛑 **Deployment Cancelled.**", ephemeral=True)

async def execute_wargame(interaction: discord.Interaction, raid_type: str, dropdown_msg: discord.Message, original_cmd_msg: discord.Message):
    channel = interaction.channel
    guild_id = interaction.guild.id
    
    raid_title = RAID_LABELS.get(raid_type, "Unknown Protocol")
    
    status_embed = discord.Embed(title=f"⚡ {raid_title} Active", description="Injecting threats and contextual false positives...", color=discord.Color.dark_purple())
    status_msg = await channel.send(embed=status_embed)
    
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
    
    try:
        webhook = None
        existing_webhooks = await channel.webhooks()
        for wh in existing_webhooks:
            if wh.name.startswith("Sylas"):
                webhook = wh
                break
                
        if not webhook:
            webhook = await channel.create_webhook(name="Sylas_Ghost_Matrix")
        
        await status_msg.edit(embed=discord.Embed(title=f"⚔️ {raid_title} Deployed", description="Monitoring channel for Mod Response...", color=discord.Color.red()))
        
        active_wargames[game_id] = {
            "status_msg_id": status_msg.id, "dropdown_msg_id": dropdown_msg.id, 
            "channel_id": channel.id, "start_time": discord.utils.utcnow(),
            "scams_left": scam_count, "failed": False, "cancelled": False, "msg_map": {},
            "attempts": 0, "guild_id": guild_id, "raid_type": raid_type,
            "raid_title": raid_title
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
        active_guild_sessions.discard(guild_id)
        post_raid_cooldowns[guild_id] = datetime.datetime.utcnow()
            
        wargame = active_wargames.get(game_id)
        
        # Temporal buffer heuristic to avoid missing the Discord API bulk execution window
        await asyncio.sleep(1.5) 
        
        if wargame and not wargame.get("cancelled"):
            final_embed = discord.Embed(title=f"✅ {raid_title.upper()} COMPLETE")
            if wargame["failed"]:
                final_embed.title = f"❌ {raid_title.upper()} FAILED"
                final_embed.description = "A moderator deleted an innocent message. Structural integrity compromised."
                final_embed.color = discord.Color.red()
            elif wargame["scams_left"] > 0:
                final_embed.title = f"❌ {raid_title.upper()} FAILED (TIMEOUT)"
                final_embed.description = f"Mods failed to delete {wargame['scams_left']} threats within 60 seconds."
                final_embed.color = discord.Color.orange()
            else:
                final_embed.description = "All threats neutralized successfully without casualties. Perfect execution."
                final_embed.color = discord.Color.green()

            try: 
                await status_msg.edit(embed=final_embed)
                await status_msg.delete(delay=15.0)
            except Exception: 
                # Fallback: if the status message was somehow wiped, queue the debounce sender
                bot.loop.create_task(send_delayed_notice(channel.id, embed=final_embed))
                
        elif wargame and wargame.get("cancelled"):
            try: await status_msg.delete()
            except: pass

            if wargame.get("attempts", 0) == 0:
                from db import guild_cooldowns
                await guild_cooldowns.delete_one({"guild_id": str(wargame["guild_id"]), "raid_type": wargame["raid_type"]})

            if wargame.get("cancelled_reason") == "purge":
                # Spawns a background task that waits for the purge to finish entirely before sending the message
                bot.loop.create_task(send_delayed_notice(channel.id, content=f"🛑 **{raid_title} Terminated.** Control matrix was destroyed by a massive channel purge."))
            
        active_wargames.pop(game_id, None)

        for msg in spawned_msgs:
            try: await msg.delete()
            except: pass

        try: await dropdown_msg.delete()
        except: pass
        try: await original_cmd_msg.delete()
        except: pass


# --- UNIFIED DELETION TRACKING (SINGLE & BULK PURGE SUPPORT) ---
async def handle_message_deletion(message_id: int, channel_id: int):
    """Processes standard deletions and massive discord purges synchronously"""
    if message_id in pending_dropdowns:
        meta = pending_dropdowns.pop(message_id)
        guild_id = meta["guild_id"]
        
        active_guild_sessions.discard(guild_id)
        post_raid_cooldowns[guild_id] = datetime.datetime.utcnow()
            
        # Spawn the debounce background task
        bot.loop.create_task(send_delayed_notice(channel_id, content="🛑 **Deployment Aborted.** Selection interface was destroyed via purge."))
        return

    for game_id, wargame in list(active_wargames.items()):
        if message_id in [wargame.get("status_msg_id"), wargame.get("dropdown_msg_id")]:
            if not wargame.get("cancelled"):
                wargame["cancelled"] = True
                wargame["cancelled_reason"] = "purge"
            continue

        if message_id in wargame["msg_map"]:
            wargame["attempts"] += 1
            is_malicious = wargame["msg_map"][message_id]
            time_alive = (discord.utils.utcnow() - wargame["start_time"]).total_seconds()
            
            try:
                channel = bot.get_channel(wargame["channel_id"])
                if channel:
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

@bot.event
async def on_raw_message_delete(payload):
    # Log activity for the Adaptive Debouncer
    channel_delete_activity[payload.channel_id] = asyncio.get_event_loop().time()
    await handle_message_deletion(payload.message_id, payload.channel_id)

@bot.event
async def on_raw_bulk_message_delete(payload):
    """Secures against Discord API Mass-Purge evasion tactics"""
    # Log activity for the Adaptive Debouncer
    channel_delete_activity[payload.channel_id] = asyncio.get_event_loop().time()
    for msg_id in payload.message_ids:
        await handle_message_deletion(msg_id, payload.channel_id)

async def start_bot():
    await bot.start(DISCORD_TOKEN)
