import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_payloads(intensity: int, raid_type: str = "phishing", primary_model="llama3"):
    """Generates an array of personas and payloads based on the selected raid type."""
    
    # Fast Fallback payloads depending on the selected raid type
    if raid_type == "ping":
        fallback = [{"username": "Announcer", "spam_message": "🚨 @everyone CRITICAL ALERT: Verify your account now or face a ban! https://fake-discord-verify.com"}] * intensity
    else:
        fallback = [{"username": f"Ghost_{i}", "spam_message": "Hey! I just got 3 months of free Nitro, click here: https://fake-nitro-drop.com"} for i in range(intensity)]
    
    async with ai_semaphore:
        try:
            # Context-Aware AI Generation
            prompt_context = "Generate a JSON array of Discord phishing scams."
            if raid_type == "ping":
                prompt_context = "Generate a JSON array of urgent Discord announcements that would maliciously ping @everyone."
            
            system_prompt = (
                f"{prompt_context} Create {intensity} objects. "
                "Keys MUST be 'username' and 'spam_message'. "
                "Make them sound realistic. Output ONLY raw JSON array."
            )
            
            # FAST TIMEOUT (10s): If the AI VM is choking, instantly drop to fallback instead of hanging
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": primary_model, "prompt": system_prompt, "stream": False, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                
                if raw.startswith("```json"): raw = raw[7:]
                if raw.endswith("```"): raw = raw[:-3]
                    
                payloads = json.loads(raw)
                if isinstance(payloads, list) and len(payloads) > 0 and "username" in payloads[0]:
                    return payloads
                
        except Exception as e:
            print(f"⚠️ AI generation failed ({e}). Deploying hardcoded {raid_type} fallbacks to keep raid active.")
            
    return fallback
