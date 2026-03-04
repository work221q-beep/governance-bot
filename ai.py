import os, httpx, asyncio, json, re
from datetime import datetime
from db import payload_armory

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def harvest_payloads(raid_type: str = "phishing"):
    prompt_context = "Generate a JSON array of 5 highly deceptive Discord phishing scams. Make them sound like a real, slightly toxic gamer or a compromised moderator."
    if raid_type == "ping":
        prompt_context = "Generate a JSON array of 5 urgent, panic-inducing Discord announcements that maliciously ping @everyone. Make them look official but fake."
    
    system_prompt = (
        f"{prompt_context} You MUST output ONLY a valid JSON array. "
        "Each object MUST have keys: 'username' and 'spam_message'."
    )
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{OLLAMA_URL}/api/generate", json={"model": "llama3", "prompt": system_prompt, "stream": False, "format": "json"})
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
            
            json_match = re.search(r'\[.*\]', raw, re.DOTALL) or re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                payloads = parsed if isinstance(parsed, list) else (list(parsed.values())[0] if isinstance(parsed, dict) else [])
                            
                inserts = [{"username": str(p["username"]), "spam_message": str(p["spam_message"]), "raid_type": raid_type, "model": "llama3", "created_at": datetime.utcnow()} for p in payloads if isinstance(p, dict) and "username" in p and "spam_message" in p]
                if inserts: await payload_armory.insert_many(inserts)
    except Exception as e:
        print(f"🚨 AI ERROR: {e}")

async def harvest_loop():
    await asyncio.sleep(5) 
    while True:
        try:
            for raid_type in ["phishing", "ping"]:
                if await payload_armory.count_documents({"raid_type": raid_type}) < 25:
                    await harvest_payloads(raid_type)
        except Exception: pass
        await asyncio.sleep(300)

async def get_preloaded_payloads(intensity: int, raid_type: str = "phishing"):
    cursor = payload_armory.aggregate([{"$match": {"raid_type": raid_type}}, {"$sample": {"size": intensity}}])
    payloads = await cursor.to_list(length=intensity)
    if not payloads:
        return [{"username": "System", "spam_message": "⚠️ AI Armory Empty. Test Payload."}] * intensity
    return payloads
