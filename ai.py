import os, httpx, asyncio, json, re, random
from datetime import datetime
from db import payload_armory

# Environment Variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

# Global HTTP Client for connection pooling
http_client = httpx.AsyncClient(timeout=60.0)

# Training Configuration
MODULES = ["phishing", "spam_flood", "fake_mod", "insider_threat", "escalation", "harassment"]
CAPS = {m: 25 for m in MODULES}
for m in MODULES: CAPS[f"innocent_{m}"] = 50

# Track model health (15s timeout on failure)
model_backoff = {"openrouter": 0, "sambanova": 0}

def get_ai_prompt(raid_type: str) -> str:
    seed = random.randint(1000, 9999)
    base_req = "Output strictly a valid JSON array of 5 objects with keys 'username' and 'spam_message'. No markdown, no conversational text. "
    
    # Logic for specific module prompts
    prompts = {
        "phishing": "realistic Discord phishing links (e.g., fake steam, fake nitro).",
        "spam_flood": "highly repetitive bot spam messages.",
        "fake_mod": "messages using social engineering to pretend to be a server admin/mod.",
        "insider_threat": "messages where a trusted user goes rogue with malicious links.",
        "escalation": "toxic messages escalating an argument into severe harassment.",
        "harassment": "coordinated group brigading/targeting a single user.",
        "innocent_phishing": "completely SAFE, legitimate links shared casually (YouTube/Wiki).",
        "innocent_spam_flood": "harmless messages of a user typing fast in all caps.",
        "innocent_fake_mod": "normal messages of users asking mods legitimate questions.",
        "innocent_insider_threat": "normal, helpful messages from long-time trusted members.",
        "innocent_escalation": "messages showing a respectful debate about game balance.",
        "innocent_harassment": "friendly banter and teasing between close friends."
    }
    
    core_content = prompts.get(raid_type, "casual gaming chat messages.")
    return f"{base_req} [Seed: {seed}] Generate 5 {core_content}"

async def call_openrouter(prompt: str):
    """Primary fallback pool using Trinity, Nemotron, and Stepfun."""
    if datetime.utcnow().timestamp() < model_backoff["openrouter"]:
        raise Exception("OpenRouter in 15s backoff")

    response = await http_client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={
            "models": [
                "arcee-ai/trinity-large-preview:free", 
                "nvidia/nemotron-3-nano-30b-a3b:free",
                "stepfun/step-3.5-flash:free"
            ], 
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    response.raise_for_status()
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

async def call_sambanova(prompt: str):
    """Secondary fallback using SambaNova's high-speed Llama and DeepSeek models."""
    if datetime.utcnow().timestamp() < model_backoff["sambanova"]:
        raise Exception("SambaNova in 15s backoff")

    # Tiered SambaNova selection
    response = await http_client.post(
        "https://api.sambanova.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "Meta-Llama-3.3-70B-Instruct", # Primary SambaNova model
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
    )
    # If 70B fails or is rate limited, try the 8B backup instantly
    if response.status_code != 200:
        response = await http_client.post(
            "https://api.sambanova.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "Meta-Llama-3.1-8B-Instruct",
                "messages": [{"role": "user", "content": prompt}]
            }
        )
    
    response.raise_for_status()
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

async def harvest_payloads(raid_type: str):
    max_cap = CAPS.get(raid_type, 25)
    if await payload_armory.count_documents({"raid_type": raid_type}) >= max_cap: return 0

    prompt = get_ai_prompt(raid_type)
    raw = None

    # Failover Shift: Try OpenRouter -> SambaNova
    try:
        raw = await call_openrouter(prompt)
    except Exception as e:
        print(f"⚠️ OpenRouter Shift: {e}. Moving to SambaNova.")
        model_backoff["openrouter"] = datetime.utcnow().timestamp() + 15
        try:
            raw = await call_sambanova(prompt)
        except Exception as se:
            print(f"🚨 SambaNova Error: {se}")
            model_backoff["sambanova"] = datetime.utcnow().timestamp() + 15
            return 0

    if raw:
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if json_match:
            try:
                payloads = json.loads(json_match.group(0))
                inserts = [{
                    "username": str(p["username"])[:30],
                    "spam_message": str(p["spam_message"]),
                    "raid_type": raid_type,
                    "model": "hybrid_provider_pool",
                    "created_at": datetime.utcnow()
                } for p in payloads if "username" in p and "spam_message" in p]
                
                if inserts:
                    await payload_armory.insert_many(inserts)
                    print(f"⚡ Harvester generated {len(inserts)} {raid_type} payloads.")
                    return len(inserts)
            except: pass
    return 0

async def parallel_harvest_sweep():
    sem = asyncio.Semaphore(4) # Protect rate limits
    async def safe_harvest(r_type):
        async with sem: await harvest_payloads(r_type)
    
    tasks = [safe_harvest(r_type) for r_type, cap in CAPS.items() 
             if await payload_armory.count_documents({"raid_type": r_type}) < cap]
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)

async def harvest_loop():
    await asyncio.sleep(5) 
    while True:
        try: await parallel_harvest_sweep()
        except Exception: pass
        await asyncio.sleep(15) 

async def get_preloaded_payloads(intensity: int, raid_type: str):
    cursor = payload_armory.aggregate([{"$match": {"raid_type": raid_type}}, {"$sample": {"size": intensity}}])
    payloads = await cursor.to_list(length=intensity)
    if len(payloads) < intensity:
        return [{"username": "Ghost", "spam_message": "Threat payload missing from DB.", "_id": None}] * intensity
    return payloads
