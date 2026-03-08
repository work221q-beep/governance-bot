import os, httpx, asyncio, json, re, random
from datetime import datetime
from db import payload_armory
from crypto import encrypt_data, decrypt_data

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

http_client = httpx.AsyncClient(timeout=60.0)

MODULES = ["phishing", "spam_flood", "fake_mod", "insider_threat", "escalation", "harassment"]
CAPS = {m: 60 for m in MODULES}
for m in MODULES: CAPS[f"innocent_{m}"] = 60

model_backoff = {"openrouter": 0, "sambanova": 0}

def get_ai_prompt(raid_type: str) -> str:
    seed = random.randint(10000, 99999)
    topics = ["gaming", "crypto", "nitro", "drama", "support", "giveaway", "memes", "general chat", "esports", "streaming", "art", "tech"]
    topic = random.choice(topics)
    base_req = "Output strictly a valid JSON array of 5 objects with keys 'username' and 'spam_message'. No markdown, no conversational text. "
    
    prompts = {
        "phishing": f"realistic Discord phishing links (e.g., fake steam, fake nitro) disguised as {topic} discussions.",
        "spam_flood": f"highly repetitive bot spam messages about {topic}.",
        "fake_mod": f"messages using social engineering to pretend to be a server admin/mod discussing {topic}.",
        "insider_threat": f"messages where a trusted user goes rogue with malicious links related to {topic}.",
        "escalation": f"toxic messages escalating an argument into severe harassment about {topic}.",
        "harassment": f"coordinated group brigading/targeting a single user over {topic}.",
        "innocent_phishing": f"completely SAFE, legitimate links shared casually (YouTube/Wiki) about {topic}.",
        "innocent_spam_flood": f"harmless messages of a user typing fast in all caps about {topic}.",
        "innocent_fake_mod": f"normal messages of users asking mods legitimate questions regarding {topic}.",
        "innocent_insider_threat": f"normal, helpful messages from long-time trusted members about {topic}.",
        "innocent_escalation": f"messages showing a respectful debate about {topic}.",
        "innocent_harassment": f"friendly banter and teasing between close friends discussing {topic}."
    }
    
    core_content = prompts.get(raid_type, "casual gaming chat messages.")
    return f"{base_req}[Seed: {seed}, Context: {topic}] Generate 5 {core_content}"

async def call_openrouter(prompt: str):
    if datetime.utcnow().timestamp() < model_backoff["openrouter"]:
        raise Exception("OpenRouter in backoff")
    response = await http_client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={"models":["arcee-ai/trinity-large-preview:free", "nvidia/nemotron-3-nano-30b-a3b:free"], "messages":[{"role": "user", "content": prompt}]}
    )
    response.raise_for_status()
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

async def call_sambanova(prompt: str):
    if datetime.utcnow().timestamp() < model_backoff["sambanova"]:
        raise Exception("SambaNova in backoff")
    response = await http_client.post(
        "https://api.sambanova.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"},
        json={"model": "Meta-Llama-3.3-70B-Instruct", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
    )
    response.raise_for_status()
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

# GUARANTEED PARSER FIX: Rips objects directly from text. Cannot crash on truncated JSON.
def extract_payloads_safely(raw_text: str):
    payloads = []
    try:
        # Standard fast parse if perfect
        json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
        if json_match: return json.loads(json_match.group(0))
    except Exception: pass
    
    # Aggressive fallback extraction for cut-off LLM strings
    try:
        matches = re.finditer(r'\{\s*"username"\s*:\s*"([^"]+)"\s*,\s*"spam_message"\s*:\s*"([^"]+)"', raw_text)
        for match in matches:
            payloads.append({"username": match.group(1), "spam_message": match.group(2)})
    except Exception: pass
    return payloads

async def harvest_payloads(raid_type: str):
    max_cap = CAPS.get(raid_type, 60)
    if await payload_armory.count_documents({"raid_type": raid_type}) >= max_cap: return 0

    prompt = get_ai_prompt(raid_type)
    raw = None

    try:
        raw = await call_openrouter(prompt)
    except Exception as e:
        model_backoff["openrouter"] = datetime.utcnow().timestamp() + 15
        try:
            raw = await call_sambanova(prompt)
        except Exception as se:
            model_backoff["sambanova"] = datetime.utcnow().timestamp() + 15
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
                print(f"⚡ Harvester generated {len(inserts)} {raid_type} payloads.")
                return len(inserts)
    return 0

async def parallel_harvest_sweep():
    sem = asyncio.Semaphore(10) # Speed increased heavily for rapid free-tier scaling
    async def safe_harvest(r_type):
        try:
            async with sem: await harvest_payloads(r_type)
        except Exception: pass
            
    tasks =[safe_harvest(r_type) for r_type, cap in CAPS.items() 
             if await payload_armory.count_documents({"raid_type": r_type}) < cap]
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)

async def harvest_loop():
    await asyncio.sleep(5) 
    while True:
        try: await parallel_harvest_sweep()
        except Exception: pass
        await asyncio.sleep(5) # Delay dramatically reduced to keep Armory saturated

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