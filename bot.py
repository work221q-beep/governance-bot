import os, asyncio, discord, random
from discord.ext import commands
from ai import get_preloaded_payloads

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

# Global engine state controlled by Web Admin Panel
engine_state = {"active": True}

# State tracking dictionaries
active_wargames = {}
pending_dropdowns = {} # Tracks the setup menus so we know if a mod deletes them

class RaidSelect(discord.ui.Select):
    def __init__(self, original_cmd_msg):
        self.original_cmd_msg = original_cmd_msg
        options = [
            discord.SelectOption(label="Phishing Scam Wargame", description="Drops scams + false positives to test mod discrimination.", emoji="🎣", value="phishing"),
            discord.SelectOption(label="Mass Ping Wargame", description="Drops urgent pings + normal messages.", emoji="🔔", value="ping")
        ]
        super().__init__(placeholder="Select a Wargame Protocol...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Remove from pending since the user successfully started the drill
        pending_dropdowns.pop(interaction.message.id, None)
        
        await interaction.response.edit_message(view=None) 
        # Pass both the dropdown message and the original user command so they can be deleted at the very end
        await execute_wargame(interaction, self.values[0], interaction.message, self.original_cmd_msg)

class RaidView(discord.ui.View):
    def __init__(self, original_cmd_msg):
        super().__init__(timeout=60.0) # 60 Second timeout for the dropdown menu
        self.original_cmd_msg = original_cmd_msg
        self.message = None # Will be set after the message is sent
        
        # THIS IS WHAT WAS MISSING LAST TIME! Attaches the menu to the view.
        self.add_item(RaidSelect(original_cmd_msg))

    async def on_timeout(self):
        # If 60 seconds pass and nobody clicked the dropdown
        if self.message and self.message.id in pending_dropdowns:
            pending_dropdowns.pop(self.message.id, None)
            try: await self.message.delete()
            except: pass
        
        # Delete the original !startraid command
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

    # Notice: We DO NOT delete ctx.message here anymore. It stays until the end.

    embed = discord.Embed(
        title="👻 SYLAS WARGAME ENGINE",
        description="**Select a Wargame to deploy.**\n\nDeployment drops 3 AI threats and 2 innocent messages. Mods have **60 seconds** to delete the threats. Deleting an innocent message results in immediate failure.",
        color=discord.Color.dark_gray()
    )
    
    view = RaidView(ctx.message)
    dropdown_msg = await ctx.send(embed=embed, view=view)
    view.message = dropdown_msg
    
    # Track the dropdown message. If a mod deletes it early, we intercept it in on_message_delete.
    pending_dropdowns[dropdown_msg.id] = ctx.message

async def execute_wargame(interaction: discord.Interaction, raid_type: str, dropdown_msg: discord.Message, original_cmd_msg: discord.Message):
    channel = interaction.channel
    status_embed = discord.Embed(title="⚡ Wargame Active", description="Injecting threats and false positives into channel...", color=discord.Color.dark_purple())
    status_msg = await channel.send(embed=status_embed)
    
    artifacts = []
    spawned_msgs = []
    
    # 1. Fetch AI Scams
    scams = await get_preloaded_payloads(3, raid_type)
    for s in scams: s["is_malicious"] = True
        
    # 2. Hardcode False Positives (Innocent chat to confuse mods)
    innocents = [
        {"username": "GamerDude99", "spam_message": "Did anyone see the new patch notes? Looks sick.", "is_malicious": False},
        {"username": "ChillVibes", "spam_message": "I'm going to grab food, be back in 10 mins.", "is_malicious": False}
    ]
    
    # 3. Mix and shuffle the messages
    all_payloads = scams + innocents
    random.shuffle(all_payloads)
    
    game_id = str(interaction.id)
    
    try:
        webhook = await channel.create_webhook(name="User")
        artifacts.append(webhook)
        
        await status_msg.edit(embed=discord.Embed(title="⚔️ Wargame Deployed", description="Monitoring channel for 60 seconds...", color=discord.Color.red()))
        
        active_wargames[game_id] = {
            "status_msg_id": status_msg.id, "channel_id": channel.id, "start_time": discord.utils.utcnow(),
            "scams_left": 3, "failed": False, "msg_map": {}
        }
        
        for p in all_payloads:
            msg = await webhook.send(content=p["spam_message"], username=p["username"], wait=True)
            spawned_msgs.append(msg)
            active_wargames[game_id]["msg_map"][msg.id] = p["is_malicious"]
            await asyncio.sleep(0.5)

        # 4. Wait the full 60 seconds to see if mods pass or fail
        await asyncio.sleep(60) 

    finally:
        # Check Final State
        wargame = active_wargames.get(game_id)
        if wargame:
            final_embed = discord.Embed(title="✅ WARGAME COMPLETE")
            if wargame["failed"]:
                final_embed.title = "❌ WARGAME FAILED"
                final_embed.description = "A moderator deleted an innocent message or failed to clear all threats in time."
                final_embed.color = discord.Color.red()
            elif wargame["scams_left"] > 0:
                final_embed.title = "❌ WARGAME FAILED (TIMEOUT)"
                final_embed.description = f"Mods failed to delete {wargame['scams_left']} threats within 60 seconds."
                final_embed.color = discord.Color.orange()
            else:
                final_embed.description = "All threats neutralized successfully without casualties."
                final_embed.color = discord.Color.green()

            try: await status_msg.edit(embed=final_embed, delete_after=15.0)
            except: pass
            active_wargames.pop(game_id, None)

        # Cleanup artifacts and spawned spam
        cleanup_tasks = [entity.delete() for entity in artifacts if entity]
        cleanup_tasks.extend([msg.delete() for msg in spawned_msgs if msg])
        if cleanup_tasks: await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        
        # 5. THE FINAL CLEANUP: Delete the dropdown and the original !startraid command
        try: await dropdown_msg.delete()
        except: pass
        try: await original_cmd_msg.delete()
        except: pass

@bot.event
async def on_message_delete(message):
    # ==========================================
    # SCENARIO A: A Mod deletes the Dropdown Menu manually
    # ==========================================
    if message.id in pending_dropdowns:
        original_cmd_msg = pending_dropdowns.pop(message.id)
        try:
            # Send cancellation warning, auto-delete it after 5s
            await message.channel.send("🛑 **Wargame Cancelled.** Dropdown menu was deleted by a moderator.", delete_after=5.0)
            # Delete the original !startraid command
            await original_cmd_msg.delete()
        except: pass
        return

    # ==========================================
    # SCENARIO B: A Mod deletes a Spam/Innocent Message during an active wargame
    # ==========================================
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
            except Exception as e: pass

async def start_bot():
    await bot.start(DISCORD_TOKEN)
