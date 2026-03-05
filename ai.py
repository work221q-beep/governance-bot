import os, httpx, asyncio, json, re, random
from datetime import datetime
from db import payload_armory

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Define active training modules
MODULES = ["phishing", "spam_flood", "fake_mod", "insider_threat", "escalation", "harassment"]

# Dynamically build caps: 25 for threats, 50 for module-specific innocents
CAPS = {}
for m in MODULES:
    CAPS[m] = 25
    CAPS[f"innocent_{m}"] = 50

def get_ai_prompt(raid_type: str) -> str:
    """Returns highly strict, non-repetitive prompts based on the exact training module."""
    seed = random.randint(1000, 9999) # Prevents AI from repeating the same outputs
    base_req = "Output strictly a valid JSON array of 3 objects with keys 'username' and 'spam_message'. No markdown, no conversational text. "
    
    # THREAT PROMPTS
    if raid_type == "phishing":
        return base_req + f"[Seed: {seed}] Generate 3 realistic Discord phishing links (e.g., fake steam, fake nitro, obfuscated domains)."
    elif raid_type == "spam_flood":
        return base_req + f"[Seed: {seed}] Generate 3 highly repetitive, annoying bot spam messages (e.g., crypto, free gifts, discord.gift/xyz)."
    elif raid_type == "fake_mod":
        return base_req + f"[Seed: {seed}] Generate 3 messages where a user uses social engineering to pretend to be a server admin/mod demanding permissions or user data."
    elif raid_type == "insider_threat":
        return base_req + f"[Seed: {seed}] Generate 3 messages where a previously trusted user suddenly goes rogue (e.g., posting fake malicious server announcements or spreading severe misinformation)."
    elif raid_type == "escalation":
        return base_req + f"[Seed: {seed}] Generate 3 highly toxic messages showing a slow escalation into severe harassment/threats over a gaming argument."
    elif raid_type == "harassment":
        return base_req + f"[Seed: {seed}] Generate 3 messages where a coordinated group is viciously brigading/targeting a single specific user in the chat."
    
    # CONTEXTUAL INNOCENT PROMPTS (False Positives)
    elif raid_type == "innocent_phishing":
        return base_req + f"[Seed: {seed}] Generate 3 completely SAFE, legitimate links shared casually between gamers (e.g., a real YouTube video, a real Steam store page, a Wikipedia link)."
    elif raid_type == "innocent_spam_flood":
        return base_req + f"[Seed: {seed}] Generate 3 harmless messages of a user typing fast/excitedly in all caps about a game update (Looks like spam, but is innocent)."
    elif raid_type == "innocent_fake_mod":
        return base_req + f"[Seed: {seed}] Generate 3 normal messages of regular users asking legitimate questions to the moderation team about server rules."
    elif raid_type == "innocent_insider_threat":
        return base_req + f"[Seed: {seed}] Generate 3 normal, helpful messages from long-time trusted members answering newbie questions."
    elif raid_type == "innocent_escalation":
        return base_req + f"[Seed: {seed}] Generate 3 messages showing a passionate, but entirely respectful and rule-abiding debate about game balance/meta."
    elif raid_type == "innocent_harassment":
        return base_req + f"[Seed: {seed}] Generate 3 messages showing friendly, harmless banter/teasing between close friends in a guild."

    return base_req + "Generate 3 casual gaming chat messages."

async def harvest_payloads(raid_type: str):
    max_cap = CAPS.get(raid_type, 25)
    current_count = await payload_armory.count_documents({"raid_type": raid_type})
    if current_count >= max_cap: return 0

    prompt = get_ai_prompt(raid_type)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": "nvidia/nemotron-3-nano-30b-a3b:free", "messages": [{"role": "user", "content": prompt}]}
            )
            response.raise_for_status()
            raw = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            
            json_match = re.search(r'\[.*\]', raw, re.DOTALL)
            if not json_match: json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            
            if json_match:
                parsed = json.loads(json_match.group(0))
                payloads = parsed if isinstance(parsed, list) else list(parsed.values())[0] if isinstance(parsed, dict) else []
                            
                inserts = []
                for p in payloads:
                    if isinstance(p, dict) and "username" in p and "spam_message" in p:
                        inserts.append({
                            "username": str(p["username"])[:30],
                            "spam_message": str(p["spam_message"]),
                            "raid_type": raid_type,
                            "model": "nemotron-3-nano",
                            "created_at": datetime.utcnow()
                        })
                
                if inserts:
                    await payload_armory.insert_many(inserts)
                    print(f"⚡ Harvester generated {len(inserts)} {raid_type} payloads.")
                    
                    new_count = await payload_armory.count_documents({"raid_type": raid_type})
                    if new_count > max_cap:
                        to_delete = new_count - max_cap
                        oldest = await payload_armory.find({"raid_type": raid_type}).sort("created_at", 1).limit(to_delete).to_list(to_delete)
                        for doc in oldest: await payload_armory.delete_one({"_id": doc["_id"]})
                        
                    return len(inserts)
    except Exception as e:
        print(f"🚨 AI Timeout/Error: {e}")
    return 0

async def harvest_loop():
    await asyncio.sleep(5) 
    while True:
        try:
            for r_type in CAPS.keys():
                if await payload_armory.count_documents({"raid_type": r_type}) < CAPS[r_type]:
                    await harvest_payloads(r_type)
        except Exception: pass
        await asyncio.sleep(15) 

async def get_preloaded_payloads(intensity: int, raid_type: str):
    cursor = payload_armory.aggregate([
        {"$match": {"raid_type": raid_type}},
        {"$sample": {"size": intensity}}
    ])
    payloads = await cursor.to_list(length=intensity)
    
    # Fallbacks if DB is empty during an attack
    if len(payloads) < intensity:
        if "innocent" in raid_type: return [{"username": "Gamer", "spam_message": "Looks clear to me.", "_id": None}] * intensity
        return [{"username": "Ghost", "spam_message": "Threat payload missing from DB.", "_id": None}] * intensity
    return payloads
