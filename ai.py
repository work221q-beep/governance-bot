import discord
import aiohttp
import time
import os
from discord.ext import commands
from utils.checks import is_mod
from utils.ai_rate import ai_rate_limited, ai_lock

# Grab the OpenRouter key instead of OLLAMA_URL
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.guild_last_ai = {}

    @commands.command()
    @is_mod()
    async def analyze(self, ctx, *args):
        if ai_rate_limited(ctx.author.id):
            await ctx.send("⏳ AI cooldown active.")
            return

        if ai_lock.locked():
            await ctx.send("⛔ AI is currently analyzing another request.")
            return

        GUILD_COOLDOWN = 15
        now = time.time()

        if ctx.guild.id in self.guild_last_ai:
            if now - self.guild_last_ai[ctx.guild.id] < GUILD_COOLDOWN:
                await ctx.send("⏳ Server AI cooldown active.")
                return

        self.guild_last_ai[ctx.guild.id] = now

        target = None
        limit = 30

        for arg in args:
            if arg.isdigit():
                limit = min(int(arg), 50)
            else:
                try:
                    target = await commands.MemberConverter().convert(ctx, arg)
                except:
                    pass

        messages = []

        async for msg in ctx.channel.history(limit=limit):
            if msg.author.bot:
                continue
            if target and msg.author != target:
                continue
            if not msg.content.strip():
                continue

            messages.append(f"{msg.author}: {msg.content}")

        if len(messages) < 2:
            await ctx.send("ℹ️ Not enough messages to analyze.")
            return

        messages.reverse()
        transcript = "\n".join(messages)

        MAX_CHARS = 3500
        if len(transcript) > MAX_CHARS:
            transcript = transcript[-MAX_CHARS:]

        prompt = f"""
You are a strict Discord moderation AI.

Be concise.
If no violations are found, respond with:
NO VIOLATIONS DETECTED

Analyze this conversation:

{transcript}

Return:
- Violations (quote exact message)
- Who said it
- Risk level (LOW/MEDIUM/HIGH)
- Short summary
- Recommended action
"""

        status_msg = await ctx.send("🧠 AI analyzing with NVIDIA Nemotron...")

        async def run_ai():
            async with ai_lock:
                if not OPENROUTER_API_KEY:
                    await ctx.send("⚠️ API Key missing. Cannot contact OpenRouter.")
                    return

                try:
                    # OpenRouter Endpoint
                    endpoint = "https://openrouter.ai/api/v1/chat/completions"
                    timeout = aiohttp.ClientTimeout(total=40)

                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(
                            endpoint,
                            headers={
                                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                "HTTP-Referer": "https://sylas-engine.onrender.com",
                                "X-Title": "Sylas Red Team Engine"
                            },
                            json={
                                "model": "nvidia/nemotron-3-nano-30b-a3b:free",
                                "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.1,
                                "max_tokens": 150 # Replaced num_predict with max_tokens
                            }
                        ) as resp:

                            if resp.status != 200:
                                error_text = await resp.text()
                                print(f"OpenRouter Error: {error_text}")
                                await ctx.send(f"⚠️ OpenRouter AI error {resp.status}")
                                return

                            data = await resp.json()
                            # Standard OpenRouter extraction
                            result = data['choices'][0]['message']['content'].strip()

                except Exception as e:
                    print(f"AI Request Exception: {e}")
                    await ctx.send("⚠️ AI request failed to connect.")
                    return

                if not result:
                    await ctx.send("⚠️ AI returned empty response.")
                    return

                if result.upper().startswith("NO VIOLATIONS"):
                    report = "✅ **No violations detected in analyzed messages.**"
                else:
                    report = f"🧠 **NVIDIA AI Moderation Report**\n\n{result}"

                if len(report) > 1900:
                    report = report[:1900] + "\n\n[Truncated]"

                try:
                    await status_msg.delete()
                except:
                    pass

                await ctx.send(report)

        self.bot.loop.create_task(run_ai())

async def setup(bot):
    await bot.add_cog(AI(bot))
