import os, httpx, asyncio, json, re
from datetime import datetime
from db import payload_armory

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MAX_PAYLOADS = 25 # 25 Ping + 25 Phishing = 50 Total

async def harvest_payloads(raid_type: str = "phishing"):
    current_count = await payload_armory.count_documents({"raid_type": raid_type})
    if current_count >= MAX_PAYLOADS: return 0

    prompt = f"Generate a JSON array of 3 realistic Discord {raid_type} scam messages. Output strictly a valid JSON array of objects with keys 'username' and 'spam_message'. No markdown, no extra text, just JSON."
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": "llama3", "prompt": prompt, "stream": False, "format": "json"}
            )
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
            
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
                            "model": "llama3",
                            "created_at": datetime.utcnow()
                        })
                
                if inserts:
                    await payload_armory.insert_many(inserts)
                    print(f"⚡ Harvester generated {len(inserts)} {raid_type} payloads.")
                    
                    new_count = await payload_armory.count_documents({"raid_type": raid_type})
                    if new_count > MAX_PAYLOADS:
                        to_delete = new_count - MAX_PAYLOADS
                        oldest = await payload_armory.find({"raid_type": raid_type}).sort("created_at", 1).limit(to_delete).to_list(to_delete)
                        for doc in oldest: await payload_armory.delete_one({"_id": doc["_id"]})
                        
                    return len(inserts)
    except Exception as e:
        print(f"🚨 AI Error: {e}")
    return 0

async def harvest_loop():
    await asyncio.sleep(5) 
    while True:
        try:
            for raid_type in ["phishing", "ping"]:
                if await payload_armory.count_documents({"raid_type": raid_type}) < MAX_PAYLOADS:
                    await harvest_payloads(raid_type)
        except Exception: pass
        await asyncio.sleep(15) 

async def get_preloaded_payloads(intensity: int, raid_type: str = "phishing"):
    cursor = payload_armory.aggregate([
        {"$match": {"raid_type": raid_type}},
        {"$sample": {"size": intensity}}
    ])
    payloads = await cursor.to_list(length=intensity)
    
    if len(payloads) < intensity:
        if raid_type == "ping": return [{"username": "System", "spam_message": "🚨 @everyone CRITICAL ALERT: Verify your account! https://fake-verify.com"}] * intensity
        return [{"username": "Ghost", "spam_message": "Free Nitro Drop: https://fake-nitro.com"}] * intensity
    return payloads
