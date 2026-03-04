import os, httpx, asyncio, json
from datetime import datetime
from db import payload_armory

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def harvest_loop():
    """Background worker that continuously stocks the Armory with fresh AI payloads."""
    await asyncio.sleep(5) 
    print("🌾 AI Harvester initialized. Monitoring payload armory...")
    
    while True:
        try:
            for raid_type in ["phishing", "ping"]:
                count = await payload_armory.count_documents({"raid_type": raid_type})
                
                if count < 25: # Keeps a constant stock of 25 payloads per category
                    print(f"🔄 Armory low on {raid_type} payloads ({count}/25). Waking AI VM...")
                    
                    prompt_context = "Generate a JSON array of 5 Discord phishing scams."
                    if raid_type == "ping":
                        prompt_context = "Generate a JSON array of 5 urgent Discord announcements that would maliciously ping @everyone."
                    
                    system_prompt = (
                        f"{prompt_context} Keys MUST be 'username' and 'spam_message'. "
                        "Make the spam_message highly realistic, manipulative, and professional. Output ONLY a raw JSON array."
                    )
                    
                    # Generous 120s timeout because this runs in the background. It will never hang the user.
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        response = await client.post(
                            f"{OLLAMA_URL}/api/generate",
                            json={"model": "llama3", "prompt": system_prompt, "stream": False, "format": "json"}
                        )
                        response.raise_for_status()
                        raw = response.json().get("response", "").strip()
                        
                        if raw.startswith("```json"): raw = raw[7:]
                        if raw.startswith("```"): raw = raw[3:]
                        if raw.endswith("```"): raw = raw[:-3]
                            
                        payloads = json.loads(raw)
                        if isinstance(payloads, list):
                            inserts = []
                            for p in payloads:
                                if "username" in p and "spam_message" in p:
                                    inserts.append({
                                        "username": p["username"],
                                        "spam_message": p["spam_message"],
                                        "raid_type": raid_type,
                                        "model": "llama3",
                                        "created_at": datetime.utcnow()
                                    })
                            if inserts:
                                await payload_armory.insert_many(inserts)
                                print(f"✅ Harvester secured {len(inserts)} new {raid_type} payloads.")
                                
        except Exception as e:
            print(f"⚠️ Harvester error (AWS VM might be sleeping): {e}")
        
        # Rest for 5 minutes before checking inventory again
        await asyncio.sleep(300)

async def get_preloaded_payloads(intensity: int, raid_type: str = "phishing"):
    """Fetches payloads INSTANTLY from the database instead of making the user wait for the AI."""
    cursor = payload_armory.aggregate([
        {"$match": {"raid_type": raid_type}},
        {"$sample": {"size": intensity}} # Grab random payloads from the armory
    ])
    payloads = await cursor.to_list(length=intensity)
    
    # Absolute zero-fail fallback just in case the database is completely empty
    if len(payloads) < intensity:
        if raid_type == "ping":
            return [{"username": "System", "spam_message": "🚨 @everyone CRITICAL ALERT: Verify your account! [https://fake-verify.com](https://fake-verify.com)"}] * intensity
        return [{"username": "Ghost", "spam_message": "Free Nitro Drop: [https://fake-nitro.com](https://fake-nitro.com)"}] * intensity
        
    return payloads
