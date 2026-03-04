import os, httpx, asyncio, json, re
from datetime import datetime
from db import payload_armory

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def harvest_payloads(raid_type: str = "phishing"):
    """Single execution to forcefully generate payloads for a specific raid type."""
    prompt_context = "Generate a JSON array of 5 Discord phishing scams."
    if raid_type == "ping":
        prompt_context = "Generate a JSON array of 5 urgent Discord announcements that would maliciously ping @everyone."
    
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
            
            print(f"🧠 [RAW AI OUTPUT for {raid_type}]: {raw[:200]}...") # Debug log for Render
            
            # Bulletproof Regex JSON Extraction
            json_match = re.search(r'\[.*\]', raw, re.DOTALL)
            if not json_match:
                # Fallback if the AI returned an object instead of an array
                json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            
            if json_match:
                parsed = json.loads(json_match.group(0))
                payloads = []
                
                if isinstance(parsed, list):
                    payloads = parsed
                elif isinstance(parsed, dict):
                    # Find the first list inside the dictionary
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
                    print(f"✅ SUCCESS: Harvester secured {len(inserts)} new {raid_type} payloads.")
                    return len(inserts)
                else:
                    print("⚠️ AI generated JSON, but it didn't match the required schema.")
            else:
                print("⚠️ Regex failed to find any valid JSON array or object in the AI response.")
                
    except Exception as e:
        print(f"🚨 CRITICAL AI ERROR: Could not connect to AWS Ollama. Details: {e}")
    
    return 0

async def harvest_loop():
    """Background worker that continuously stocks the Armory."""
    await asyncio.sleep(5) 
    print("🌾 AI Harvester background loop initialized.")
    
    while True:
        try:
            for raid_type in ["phishing", "ping"]:
                count = await payload_armory.count_documents({"raid_type": raid_type})
                if count < 25:
                    print(f"🔄 Armory low on {raid_type} payloads ({count}/25). Waking AI VM...")
                    await harvest_payloads(raid_type)
        except Exception as e:
            print(f"⚠️ Harvester loop encountered an error: {e}")
        
        await asyncio.sleep(300) # Wait 5 minutes before checking again

async def get_preloaded_payloads(intensity: int, raid_type: str = "phishing"):
    """Fetches payloads INSTANTLY from the database."""
    cursor = payload_armory.aggregate([
        {"$match": {"raid_type": raid_type}},
        {"$sample": {"size": intensity}}
    ])
    payloads = await cursor.to_list(length=intensity)
    
    # Fallback only triggers if database is literally empty
    if len(payloads) < intensity:
        print(f"⚠️ Armory is empty for {raid_type}! Using hardcoded fallback.")
        if raid_type == "ping":
            return [{"username": "System", "spam_message": "🚨 @everyone CRITICAL ALERT: Verify your account! https://fake-verify.com"}] * intensity
        return [{"username": "Ghost", "spam_message": "Free Nitro Drop: https://fake-nitro.com"}] * intensity
        
    return payloads
