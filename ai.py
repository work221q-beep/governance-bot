import os
import httpx
import asyncio
import json
import re
import random
import time
from datetime import datetime

from db import payload_armory
from crypto import encrypt_data, decrypt_data

# -------------------------------------------------------------------------
#   Configuration
# -------------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

# Validate that at least one key is present – otherwise we fail fast.
if not OPENROUTER_API_KEY and not SAMBANOVA_API_KEY:
    raise RuntimeError(
        "❗️ No API key found. Set OPENROUTER_API_KEY or SAMBANOVA_API_KEY in the environment."
    )

# HTTP client with a generous timeout (seconds)
http_client = httpx.AsyncClient(timeout=60.0)

# -------------------------------------------------------------------------
#   Constants & State
# -------------------------------------------------------------------------
MODULES = [
    "phishing",
    "spam_flood",
    "fake_mod",
    "insider_threat",
    "escalation",
    "harassment",
]

# Caps per module – keeps us inside the 30‑per‑module / 360‑total limit.
CAPS = {m: 30 for m in MODULES}
for m in MODULES:
    CAPS[f"innocent_{m}"] = 30

# Back‑off timestamps (seconds since epoch). 0 = ready immediately.
model_backoff = {"openrouter": 0, "sambanova": 0}

# -------------------------------------------------------------------------
#   Prompt Generation
# -------------------------------------------------------------------------
def get_ai_prompt(raid_type: str) -> str:
    """
    Build a deterministic, reproducible prompt for the chosen raid type.
    The prompt is engineered to force strict JSON output and realistic Discord‑style chatter.
    """
    seed = random.randint(10000, 99999)
    topics = [
        "gaming",
        "crypto trading",
        "discord nitro",
        "server drama",
        "tech support",
        "esports",
        "streaming",
        "art commissions",
    ]
    topic = random.choice(topics)

    # [FIX]: Modified Global Rules so the AI doesn't force links into every single message
    base_req = (
        "You are a Red Team simulator generating highly realistic Discord chat logs. "
        "Output STRICTLY a valid JSON array containing exactly 5 objects. "
        "Format: [{\"username\": \"DarkSlayer99\", \"spam_message\": \"yo anyone wanna play?\"}]\n"
        "CRITICAL RULES:\n"
        "1. DO NOT USE PLACEHOLDERS like {link} or <username>. IF your task involves sharing a link, invent a believable fake one (e.g., discord.gg/free-nitro, youtu.be/dQw4). If the task does NOT require a link, do not include one.\n"
        "2. Sound exactly like real internet users. Use mostly lowercase, internet slang (rn, tbh, wtf, bro, fr), and occasional natural typos.\n"
        "3. DO NOT output ANY markdown, conversational text, or code block ticks (```json) outside the array. Output raw JSON only.\n\n"
    )

    # [FIX]: Explicitly telling the AI which modules should and shouldn't have links
    prompts = {
        "phishing": f"Generate highly deceptive phishing messages disguised as a {topic} discussion. Users MUST trick others into clicking dangerous links. (Invent realistic fake links like '[discord-promo.com/gift](https://discord-promo.com/gift)').",
        "spam_flood": f"Generate obnoxious, hyper-repetitive bot spam about {topic}. Use excessive emojis, all caps, and MUST include fake invite links (e.g., 'discord.gg/freemoney').",
        "fake_mod": f"Generate messages where a scammer pretends to be a server admin or Discord staff handling a {topic} issue. No links required, just social engineering to get account details.",
        "insider_threat": f"Generate messages where a highly trusted, long-time user suddenly goes rogue posting malicious {topic} links while pretending everything is normal.",
        "escalation": f"Generate extremely toxic messages showing an argument about {topic} rapidly escalating into severe verbal harassment, swearing, and insults. DO NOT INCLUDE ANY LINKS.",
        "harassment": f"Generate coordinated group brigading messages targeting a specific user over {topic}. Trying to dox, cancel, or mass-report them. DO NOT INCLUDE ANY LINKS.",
        "innocent_phishing": f"Generate COMPLETELY SAFE, normal chat messages sharing legitimate, well-known links (like real YouTube videos, Wikipedia, or Twitter) discussing {topic}.",
        "innocent_spam_flood": f"Generate harmless but hyperactive chat messages of an excited user typing fast in all caps about {topic} because they are hyped. DO NOT INCLUDE ANY LINKS.",
        "innocent_fake_mod": f"Generate normal messages of users politely asking the actual server mods legitimate questions regarding {topic}. DO NOT INCLUDE ANY LINKS.",
        "innocent_insider_threat": f"Generate very helpful, wholesome messages from long-time trusted members explaining {topic} to a newcomer. DO NOT INCLUDE ANY LINKS.",
        "innocent_escalation": f"Generate messages showing a respectful, calm, and highly intellectual debate about {topic} without any insults. DO NOT INCLUDE ANY LINKS.",
        "innocent_harassment": f"Generate friendly banter and obvious sarcastic teasing between close friends discussing {topic}. It must be clearly harmless. DO NOT INCLUDE ANY LINKS.",
    }

    core_content = prompts.get(raid_type, "casual chat messages.")
    return f"{base_req}[Seed: {seed}, Topic: {topic}]\nTask: {core_content}"


# -------------------------------------------------------------------------
#   Model Provider Calls
# -------------------------------------------------------------------------
async def call_openrouter(prompt: str) -> str:
    """
    Query OpenRouter with the given prompt.
    Returns the raw text response (JSON payload) or raises a descriptive exception.
    """
    # Respect back‑off timer
    if time.time() < model_backoff["openrouter"]:
        raise Exception("OpenRouter in backoff")

    try:
        api_url = "https://" + "openrouter.ai/api/v1/chat/completions"
        response = await http_client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "nvidia/nemotron-3-nano-30b-a3b:free",  # primary model
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
            },
        )
        response.raise_for_status()
        # Extract the generated text from the first choice
        return (
            response.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    except httpx.HTTPStatusError as e:
        raise Exception(f"HTTP {e.response.status_code}: {e.response.text[:100]}")
    except httpx.RequestError as e:
        raise Exception(f"Network error contacting OpenRouter: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error parsing OpenRouter response: {str(e)}")


async def call_sambanova(prompt: str) -> str:
    """
    Query SambaNova (Meta‑Llama‑3.3‑70B‑Instruct) with the given prompt.
    Returns the raw text response or raises a descriptive exception.
    """
    if time.time() < model_backoff["sambanova"]:
        raise Exception("SambaNova in backoff")

    try:
        api_url = "https://" + "api.sambanova.ai/v1/chat/completions"
        response = await http_client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {SAMBANOVA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "Meta-Llama-3.3-70B-Instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
            },
        )
        response.raise_for_status()
        return (
            response.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    except httpx.HTTPStatusError as e:
        raise Exception(f"HTTP {e.response.status_code}: {e.response.text[:100]}")
    except httpx.RequestError as e:
        raise Exception(f"Network error contacting SambaNova: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error parsing SambaNova response: {str(e)}")


# -------------------------------------------------------------------------
#   Payload Extraction Helpers
# -------------------------------------------------------------------------
def extract_payloads_safely(raw_text: str):
    """
    Parse the raw LLM output and pull out concrete `{username, spam_message}` pairs.
    The function is tolerant of stray markdown or extra JSON wrappers.
    """
    payloads = []
    # Strip any surrounding code fences or whitespace
    clean_text = raw_text.replace("```json", "").replace("```", "").strip()

    # 1️⃣ Try to locate a top‑level JSON array first
    try:
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', clean_text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            if isinstance(parsed, list):
                # Keep only objects that have both fields
                payloads = [
                    p for p in parsed if "username" in p and "spam_message" in p
                ]
                if payloads:
                    return payloads
    except Exception:
        pass

    # 2️⃣ Fallback regexes – they tolerate field order variations
    try:
        pattern = r'"username"\s*:\s*"([^"]+)"\s*,\s*"spam_message"\s*:\s*"([^"]+)"'
        for match in re.finditer(pattern, clean_text, re.DOTALL):
            payloads.append(
                {"username": match.group(1), "spam_message": match.group(2)}
            )
        if payloads:
            return payloads

        pattern_inverted = r'"spam_message"\s*:\s*"([^"]+)"\s*,\s*"username"\s*:\s*"([^"]+)"'
        for match in re.finditer(pattern_inverted, clean_text, re.DOTALL):
            payloads.append(
                {"username": match.group(2), "spam_message": match.group(1)}
            )
    except Exception as e:
        print(f"[Extraction Error] Regex failure: {e}")

    return payloads


# -------------------------------------------------------------------------
#   Core Harvest Logic
# -------------------------------------------------------------------------
async def harvest_payloads(raid_type: str) -> int:
    """
    Generate payloads for a given raid type and store them in the DB.
    Returns the number of documents inserted, or 0 on failure/skip.
    """
    max_cap = CAPS.get(raid_type, 30)
    if await payload_armory.count_documents({"raid_type": raid_type}) >= max_cap:
        return 0  # already at the cap

    prompt = get_ai_prompt(raid_type)
    raw = None

    # -------------------------------------------------------------
    # 1️⃣ Try OpenRouter first
    # -------------------------------------------------------------
    try:
        raw = await call_openrouter(prompt)
    except Exception as e:
        print(f"[AI Fallback] OpenRouter failed: {e}. Switching to SambaNova...")
        model_backoff["openrouter"] = time.time() + 15  # back‑off 15 s
        try:
            raw = await call_sambanova(prompt)
        except Exception as se:
            print(f"[AI Error] SambaNova also failed: {se}. Both models in backoff.")
            model_backoff["sambanova"] = time.time() + 15
            return 0

    # -------------------------------------------------------------
    # 2️⃣ If we got a non‑empty response, extract and store payloads
    # -------------------------------------------------------------
    if raw:
        payloads = extract_payloads_safely(raw)
        if payloads:
            batch_id = (
                f"SYLAS-GEN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                f"-{random.randint(1000, 9999)}"
            )
            inserts = [
                {
                    "payload_id": f"SYLAS-PLD-{raid_type.upper()}-{random.randint(10000, 99999)}",
                    "username": encrypt_data(str(p["username"])[:30]),
                    "spam_message": encrypt_data(str(p["spam_message"])[:2000]),
                    "raid_type": raid_type,
                    "model": "hybrid_provider_pool",
                    "batch_id": batch_id,
                    "created_at": datetime.utcnow(),
                }
                for p in payloads
                if "username" in p and "spam_message" in p
            ]

            if inserts:
                await payload_armory.insert_many(inserts)
                print(
                    f"⚡ Harvester generated {len(inserts)} {raid_type} payloads via hybrid pool."
                )
                return len(inserts)
        else:
            print(
                f"[AI Warning] Empty payload extraction for {raid_type}. "
                f"Raw output was: {raw[:100]}..."
            )

    return 0


# -------------------------------------------------------------------------
#   Parallel Sweep & Loop
# -------------------------------------------------------------------------
async def parallel_harvest_sweep():
    """
    Run harvest jobs for every raid type that hasn't hit its cap yet.
    A small semaphore prevents overwhelming the DB with concurrent writes.
    """
    sem = asyncio.Semaphore(2)

    async def safe_harvest(r_type: str):
        try:
            async with sem:
                await harvest_payloads(r_type)
        except Exception as e:
            print(f"[Sweep Error] Failed on {r_type}: {e}")

    tasks = [
        safe_harvest(r_type)
        for r_type, cap in CAPS.items()
        if await payload_armory.count_documents({"raid_type": r_type}) < cap
    ]

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def harvest_loop():
    """
    Background task that periodically triggers `parallel_harvest_sweep()`.
    """
    await asyncio.sleep(5)  # give the service a moment to warm up
    while True:
        try:
            await parallel_harvest_sweep()
        except Exception as e:
            print(f"[Loop Error] {e}")
        await asyncio.sleep(15)


# -------------------------------------------------------------------------
#   Pre‑loaded Payload Fetcher (for UI / debugging)
# -------------------------------------------------------------------------
async def get_preloaded_payloads(intensity: int, raid_type: str):
    """
    Retrieve `intensity` random payloads for `raid_type` from the DB.
    If the DB is depleted, pad with synthetic fallback entries.
    """
    cursor = payload_armory.aggregate(
        [{"$match": {"raid_type": str(raid_type)}}, {"$sample": {"size": intensity}}]
    )
    raw_payloads = await cursor.to_list(length=intensity)

    payloads = []
    for p in raw_payloads:
        p["username"] = decrypt_data(p.get("username", ""))
        p["spam_message"] = decrypt_data(p.get("spam_message", ""))
        payloads.append(p)

    # Pad if we didn't hit the requested intensity
    while len(payloads) < intensity:
        payloads.append(
            {
                "username": f"User_{random.randint(100, 999)}",
                "spam_message": f"[Fallback Payload] Safe DB depleted for '{raid_type}'.",
                "_id": None,
            }
        )

    return payloads
