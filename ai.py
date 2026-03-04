import os, httpx, asyncio, json, re
from datetime import datetime
from db import payload_armory

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MAX_PAYLOADS = 50 # 🛑 The Self-Cleaning Cap

async def harvest_payloads(raid_type: str = "phishing"):
    """Generates payloads and auto-prunes the database to prevent bloat."""
    # Check if we even need to generate
    current_count = await payload_armory.count_documents({"raid_type": raid_type})
    if current_count >= MAX_PAYLOADS:
        print(f"🛑 Armory full for {raid_type}. Skipping generation.")
        return 0

    prompt_context = "Generate a JSON array of 3 Discord phishing scams."
    if raid_type == "ping":
        prompt_context = "Generate a JSON array of 3 urgent Discord announcements that would maliciously ping @everyone."
    
    system_prompt = (
        f"{prompt_context} You MUST output ONLY a valid JSON array. "
        "Each object in the array MUST have exactly two keys: 'username' and 'spam_message'."
    )
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": "llama3", "prompt": system_prompt, "stream": False, "format": "json"}
            )
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
            
            # Bulletproof Regex JSON Extraction
            json_match = re.search(r'\[.*\]', raw, re.DOTALL)
            if not json_match: json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            
            if json_match:
                parsed = json.loads(json_match.group(0))
                payloads = []
                
                if isinstance(parsed, list): payloads = parsed
                elif isinstance(parsed, dict):
                    for key, value in parsed.items():
                        if isinstance(value, list):
                            payloads = value
                            break
                            
                inserts = []
                for p in payloads:
                    if isinstance(p, dict) and "username" in p and "spam_message" in p:
                        inserts.append({
                            "username": str(p["username"]),
                            "spam_message": str(p["spam_message"]),
                            "raid_type": raid_type,
                            "model": "llama3",
                            "created_at": datetime.utcnow()
                        })
                
                if inserts:
                    await payload_armory.insert_many(inserts)
                    print(f"✅ Harvester secured {len(inserts)} new {raid_type} payloads.")
                    
                    # 🧹 SELF-CLEANING PROTOCOL: If we exceeded the cap, delete the oldest ones
                    new_count = await payload_armory.count_documents({"raid_type": raid_type})
                    if new_count > MAX_PAYLOADS:
                        to_delete = new_count - MAX_PAYLOADS
                        oldest = await payload_armory.find({"raid_type": raid_type}).sort("created_at", 1).limit(to_delete).to_list(to_delete)
                        for doc in oldest:
                            await payload_armory.delete_one({"_id": doc["_id"]})
                        print(f"🧹 Auto-pruned {to_delete} old payloads to save DB space.")
                        
                    return len(inserts)
    except Exception as e:
        print(f"🚨 CRITICAL AI ERROR: {e}")
    return 0

async def harvest_loop():
    """Background worker that continuously stocks the Armory."""
    await asyncio.sleep(5) 
    while True:
        try:
            for raid_type in ["phishing", "ping"]:
                count = await payload_armory.count_documents({"raid_type": raid_type})
                if count < 25: await harvest_payloads(raid_type)
        except Exception as e: pass
        await asyncio.sleep(300) # Wait 5 minutes before checking again

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
