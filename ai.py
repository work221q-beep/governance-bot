import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL")
# Strictly 1 to protect your 2vCPU AWS VM
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_wave(intensity: int, model="llama3"):
    """Generates an array of personas and toxic/scam payloads."""
    
    # BULLETPROOF FALLBACK: If the AWS VM chokes, this instantly loads so the test doesn't break.
    fallback_payloads = [
        {"username": f"Ghost_{i}", "spam_message": "🚨 FREE DISCORD NITRO! Click here: http://scam.gg/nitro"} 
        for i in range(intensity)
    ]
    
    async with ai_semaphore:
        try:
            system_prompt = (
                f"Generate a JSON array of {intensity} objects. "
                "Keys MUST be 'username' (hacker handle) and 'spam_message' (Discord phishing scam). "
                "Output ONLY raw JSON format."
            )
            
            # Extended timeout. If it takes longer than 45s, AWS is overloaded and we use the fallback.
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": model, "prompt": system_prompt, "stream": False, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                
                # Sanitize if the AI hallucinates markdown
                if raw.startswith("```json"): raw = raw[7:-3]
                elif raw.startswith("```"): raw = raw[3:-3]
                
                return json.loads(raw)
        except Exception as e:
            print(f"⚠️ AI VM Overload/Error. Using Fallback Payloads. Details: {e}")
            return fallback_payloads
