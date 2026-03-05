import os, httpx, asyncio, json, re
from datetime import datetime
from db import payload_armory

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TARGET_MODEL = "nvidia/nemotron-3-nano-30b-a3b:free"

def get_max_limit(raid_type: str) -> int:
    """Returns 50 for innocent payloads, 25 for everything else."""
    return 50 if raid_type == "innocent" else 25

async def harvest_payloads(raid_type: str = "phishing"):
    max_limit = get_max_limit(raid_type)
    current_count = await payload_armory.count_documents({"raid_type": raid_type})
    if current_count >= max_limit: return 0

    # Adjust the prompt based on if it's an innocent message or a raid payload
    if raid_type == "innocent":
        prompt = "Generate a JSON array of 3 realistic, casual, and innocent Discord chat messages. Output strictly a valid JSON array of objects with keys 'username' and 'spam_message'. No markdown, no extra text."
    else:
        prompt = f"Generate a JSON array of 3 realistic Discord {raid_type} scam messages. Output strictly a valid JSON array of objects with keys 'username' and 'spam_message'. No markdown, no extra text."
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Replaced AWS/Ollama with OpenRouter
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://sylas-engine.onrender.com", # Default Render domain
                    "X-Title": "Sylas Red Team Engine"
                },
                json={
                    "model": TARGET_MODEL, 
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            response.raise_for_status()
            
            # OpenRouter standard JSON extraction
            raw = response.json()['choices'][0]['message']['content'].strip()
            
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
                            "model": TARGET_MODEL,
                            "created_at": datetime.utcnow()
                        })
                
                if inserts:
                    await payload_armory.insert_many(inserts)
                    print(f"⚡ Harvester generated {len(inserts)} {raid_type} payloads.")
                    
                    new_count = await payload_armory.count_documents({"raid_type": raid_type})
                    if new_count > max_limit:
                        to_delete = new_count - max_limit
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
            # Added "innocent" to the harvest rotation
            for raid_type in ["phishing", "ping", "innocent"]:
                max_limit = get_max_limit(raid_type)
                if await payload_armory.count_documents({"raid_type": raid_type}) < max_limit:
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
        if raid_type == "ping": return [{"username": "System", "spam_message": "🚨 @everyone CRITICAL ALERT: Verify your account! https://fake-verify.com", "_id": None}] * intensity
        if raid_type == "innocent": return [{"username": "User", "spam_message": "Hey everyone, how is it going today?", "_id": None}] * intensity
        return [{"username": "Ghost", "spam_message": "Free Nitro Drop: https://fake-nitro.com", "_id": None}] * intensity
    return payloads
