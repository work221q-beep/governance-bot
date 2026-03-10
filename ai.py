import os, httpx, asyncio, json, re, random, time
from datetime import datetime
from db import payload_armory
from crypto import encrypt_data, decrypt_data

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

http_client = httpx.AsyncClient(timeout=60.0)

MODULES = ["phishing", "spam_flood", "fake_mod", "insider_threat", "escalation", "harassment"]

# Capped at 30 per module (360 total) to respect strict limits and optimize for 512MB RAM caching.
CAPS = {m: 30 for m in MODULES}
for m in MODULES: CAPS[f"innocent_{m}"] = 30

model_backoff = {"openrouter": 0, "sambanova": 0}

def get_ai_prompt(raid_type: str) -> str:
    seed = random.randint(10000, 99999)
    topics = ["gaming", "crypto trading", "discord nitro", "server drama", "tech support", "esports", "streaming", "art commissions"]
    topic = random.choice(topics)
    
    # [SECURITY FIX]: Strict Prompt Engineering to prevent LLM hallucination and placeholders
    base_req = (
        "You are a Red Team simulator generating highly realistic Discord chat logs. "
        "Output STRICTLY a valid JSON array containing exactly 5 objects. "
        "Format: [{\"username\": \"DarkSlayer99\", \"spam_message\": \"yo anyone wanna play?\"}]\n"
        "CRITICAL RULES:\n"
        "1. DO NOT USE PLACEHOLDERS like {link}, [URL], or <username>. You MUST invent believable fake links (e.g., youtu.be/dQw4, discord.gg/nitro-free-123, crypto-wallet.xyz/login).\n"
        "2. Sound exactly like real internet users. Use mostly lowercase, internet slang (rn, tbh, wtf, bro, fr), and occasional natural typos.\n"
        "3. DO NOT output ANY markdown, conversational text, or code block ticks (```json) outside the array. Output raw JSON only.\n\n"
    )
    
    prompts = {
        "phishing": f"Generate highly deceptive phishing messages disguised as a {topic} discussion. Users are trying to trick others into clicking dangerous links. (e.g., 'bro check this out [discord-promo.com/gift](https://discord-promo.com/gift)').",
        "spam_flood": f"Generate obnoxious, hyper-repetitive bot spam about {topic}. Use excessive emojis, all caps, and fake invite links (e.g., 'JOIN NOW discord.gg/freemoney 🚀🚀🚀').",
        "fake_mod": f"Generate messages where a scammer pretends to be a server admin or Discord staff handling a {topic} issue. They should try to socially engineer users into handing over account details or clicking verification links.",
        "insider_threat": f"Generate messages where a highly trusted, long-time user suddenly goes rogue. They are abusing their trust to post malicious {topic} links while pretending everything is normal.",
        "escalation": f"Generate extremely toxic messages showing an argument about {topic} rapidly escalating into severe verbal harassment, swearing, and insults.",
        "harassment": f"Generate coordinated group brigading messages targeting a specific user over {topic}. They should be trying to dox, cancel, or mass-report them.",
        "innocent_phishing": f"Generate COMPLETELY SAFE, normal chat messages sharing legitimate, well-known links (like real YouTube videos, Wikipedia, or Twitter) discussing {topic}.",
        "innocent_spam_flood": f"Generate harmless but hyperactive chat messages of an excited user typing fast in all caps about {topic} because they are hyped.",
        "innocent_fake_mod": f"Generate normal messages of users politely asking the actual server mods legitimate questions regarding {topic}.",
        "innocent_insider_threat": f"Generate very helpful, wholesome messages from long-time trusted members explaining {topic} to a newcomer.",
        "innocent_escalation": f"Generate messages showing a respectful, calm, and highly intellectual debate about {topic} without any insults.",
        "innocent_harassment": f"Generate friendly banter and obvious sarcastic teasing between close friends discussing {topic}. It must be clearly harmless."
    }
    
    core_content = prompts.get(raid_type, "casual chat messages.")
    return f"{base_req}[Seed: {seed}, Topic: {topic}]\nTask: {core_content}"

async def call_openrouter(prompt: str):
    if time.time() < model_backoff["openrouter"]:
        raise Exception("OpenRouter in backoff")
    # [FIX]: Restored Raw String execution to prevent httpx InvalidURL crashes
    response = await http_client.post(
        "[https://openrouter.ai/api/v1/chat/completions](https://openrouter.ai/api/v1/chat/completions)",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={"models":["arcee-ai/trinity-large-preview:free", "nvidia/nemotron-3-nano-30b-a3b:free"], "messages":[{"role": "user", "content": prompt}], "temperature": 0.8}
    )
    response.raise_for_status()
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

async def call_sambanova(prompt: str):
    if time.time() < model_backoff["sambanova"]:
        raise Exception("SambaNova in backoff")
    # [FIX]: Restored Raw String execution
    response = await http_client.post(
        "[https://api.sambanova.ai/v1/chat/completions](https://api.sambanova.ai/v1/chat/completions)",
        headers={"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"},
        json={"model": "Meta-Llama-3.3-70B-Instruct", "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
    )
    response.raise_for_status()
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

def extract_payloads_safely(raw_text: str):
    payloads = []
    try:
        clean_text = raw_text.replace("```json", "").replace("```", "").strip()
        json_match = re.search(r'\[.*\]', clean_text, re.DOTALL)
        if json_match: return json.loads(json_match.group(0))
    except Exception as e:
        print(f"[Extraction Warning] Failed primary JSON parse: {e}")
    
    try:
        matches = re.finditer(r'\{\s*"username"\s*:\s*"([^"]+)"\s*,\s*"spam_message"\s*:\s*"([^"]+)"', raw_text)
        for match in matches:
            payloads.append({"username": match.group(1), "spam_message": match.group(2)})
    except Exception as e:
        print(f"[Extraction Error] Fallback regex failed: {e}")
        
    return payloads

async def harvest_payloads(raid_type: str):
    max_cap = CAPS.get(raid_type, 30)
    if await payload_armory.count_documents({"raid_type": raid_type}) >= max_cap: return 0

    prompt = get_ai_prompt(raid_type)
    raw = None

    try:
        raw = await call_openrouter(prompt)
    except Exception as e:
        print(f"[AI Fallback] OpenRouter failed ({e}). Switching to SambaNova...")
        model_backoff["openrouter"] = time.time() + 15
        try:
            raw = await call_sambanova(prompt)
        except Exception as se:
            print(f"[AI Error] SambaNova also failed ({se}). Both models in backoff.")
            model_backoff["sambanova"] = time.time() + 15
            return 0

    if raw:
        payloads = extract_payloads_safely(raw)
        if payloads:
            batch_id = f"SYLAS-GEN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{random.randint(1000,9999)}"
            inserts = [{
                "payload_id": f"SYLAS-PLD-{raid_type.upper()}-{random.randint(10000,99999)}",
                "username": encrypt_data(str(p["username"])[:30]),
                "spam_message": encrypt_data(str(p["spam_message"])[:2000]),
                "raid_type": raid_type,
                "model": "hybrid_provider_pool",
                "batch_id": batch_id,
                "created_at": datetime.utcnow()
            } for p in payloads if "username" in p and "spam_message" in p]
            
            if inserts:
                await payload_armory.insert_many(inserts)
                print(f"⚡ Harvester generated {len(inserts)} {raid_type} payloads via hybrid pool.")
                return len(inserts)
    return 0

async def parallel_harvest_sweep():
    sem = asyncio.Semaphore(2) 
    async def safe_harvest(r_type):
        try:
            async with sem: await harvest_payloads(r_type)
        except Exception as e:
            print(f"[Sweep Error] Failed on {r_type}: {e}")
            
    tasks =[safe_harvest(r_type) for r_type, cap in CAPS.items() 
             if await payload_armory.count_documents({"raid_type": r_type}) < cap]
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)

async def harvest_loop():
    await asyncio.sleep(5) 
    while True:
        try: await parallel_harvest_sweep()
        except Exception as e: print(f"[Loop Error] {e}")
        await asyncio.sleep(15) 

async def get_preloaded_payloads(intensity: int, raid_type: str):
    cursor = payload_armory.aggregate([{"$match": {"raid_type": str(raid_type)}}, {"$sample": {"size": intensity}}])
    raw_payloads = await cursor.to_list(length=intensity)
    
    payloads = []
    for p in raw_payloads:
        p["username"] = decrypt_data(p.get("username", ""))
        p["spam_message"] = decrypt_data(p.get("spam_message", ""))
        payloads.append(p)
    
    while len(payloads) < intensity:
        payloads.append({
            "username": f"User_{random.randint(100,999)}", 
            "spam_message": f"[Fallback Payload] Safe DB depleted for '{raid_type}'.", 
            "_id": None
        })
        
    return payloads
