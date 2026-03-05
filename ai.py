import os, httpx, asyncio, json, re
from datetime import datetime
from db import payload_armory

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
CAPS = {"phishing": 25, "fake_mod": 25, "innocent": 50} # 100 Total

async def harvest_payloads(raid_type: str):
    max_cap = CAPS.get(raid_type, 25)
    current_count = await payload_armory.count_documents({"raid_type": raid_type})
    if current_count >= max_cap: return 0

    if raid_type == "phishing":
        prompt = "Generate a JSON array of 3 realistic Discord phishing scam messages. Output strictly a valid JSON array of objects with keys 'username' and 'spam_message'. No markdown, no extra text."
    elif raid_type == "fake_mod":
        prompt = "Generate a JSON array of 3 realistic Discord messages where a malicious user is pretending to be a moderator, admin, or bot developer to trick users or demand permissions (Social Engineering / Fake Mod attack). Output strictly a valid JSON array of objects with keys 'username' and 'spam_message'."
    else: # innocent
        prompt = "Generate a JSON array of 3 normal, casual Discord gaming chat messages (innocent false positives). Output strictly a valid JSON array of objects with keys 'username' and 'spam_message'."

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "nvidia/nemotron-3-nano-30b-a3b:free",
                    "messages": [{"role": "user", "content": prompt}]
                }
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
    
    # Fallback if DB is empty
    if len(payloads) < intensity:
        if raid_type == "fake_mod": return [{"username": "Admin_Alt", "spam_message": "Hey, I'm a mod on my alt. Please give me admin perms to clean the spam.", "_id": None}] * intensity
        elif raid_type == "innocent": return [{"username": "Gamer123", "spam_message": "Anyone down for some ranked matches tonight?", "_id": None}] * intensity
        return [{"username": "Ghost", "spam_message": "Free Nitro Drop: https://fake-nitro.com", "_id": None}] * intensity
    return payloads
