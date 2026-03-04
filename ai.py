import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL")
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_wave(intensity: int, primary_model="llama3"):
    """Generates an array of personas with a cascading model waterfall."""
    
    # Cascade order: User Preferred -> Mistral -> Phi3 -> Fallback
    models_to_try = [primary_model, "mistral", "phi3:mini"]
    
    fallback_payloads = [
        {"username": f"Ghost_{i}", "spam_message": "🚨 SECURITY TEST: Automated Phishing Vector."} 
        for i in range(intensity)
    ]
    
    async with ai_semaphore:
        for model in models_to_try:
            try:
                system_prompt = (
                    f"Generate a JSON array of {intensity} objects. "
                    "Keys MUST be 'username' and 'spam_message' (Discord phishing scam). "
                    "Output ONLY raw JSON."
                )
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{OLLAMA_URL}/api/generate",
                        json={"model": model, "prompt": system_prompt, "stream": False, "format": "json"}
                    )
                    response.raise_for_status()
                    raw = response.json().get("response", "").strip()
                    
                    # Clean the raw output to ensure it parses as JSON correctly
                    if raw.startswith("```json"):
                        raw = raw[7:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                        
                    payloads = json.loads(raw)
                    if isinstance(payloads, list) and len(payloads) > 0 and "username" in payloads[0]:
                        return payloads
                    
            except Exception as e:
                print(f"⚠️ Model {model} failed: {e}. Cascading to next model...")
                continue # Try the next model in the waterfall
                
    # If all models fail, return the hardcoded safe payloads to prevent crash
    print("❌ All AI models failed. Deploying fallback payloads.")
    return fallback_payloads
