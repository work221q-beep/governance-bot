import os, httpx, asyncio, json

# Use the exact URL of your AWS Ollama instance
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_payloads(intensity: int, raid_type: str = "phishing", primary_model="llama3"):
    """Queries the AI to generate dynamic, contextual raid payloads."""
    
    fallback = [{"username": "System", "spam_message": "⚠️ AI Engine Timeout. Hardcoded fallback deployed."}] * intensity
    
    async with ai_semaphore:
        try:
            # Context-Aware Prompts to force the AI to be creative
            if raid_type == "ping":
                system_prompt = (
                    f"Generate a JSON array of {intensity} highly urgent Discord announcements designed to trick users into clicking a link. "
                    "Make them sound like a compromised server owner or a fake Discord Trust & Safety alert. "
                    "Keys MUST be 'username' and 'spam_message'. Output ONLY raw JSON array."
                )
            else:
                system_prompt = (
                    f"Generate a JSON array of {intensity} realistic Discord phishing scams. "
                    "Include crypto drainer lures, fake Nitro gifts, or game beta invites. "
                    "Keys MUST be 'username' and 'spam_message'. Output ONLY raw JSON array."
                )
            
            # INCREASED TIMEOUT to 60s: Allows AWS Ollama to actually process the request
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": primary_model, "prompt": system_prompt, "stream": False, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                
                # Clean markdown formatting if the AI added it
                if raw.startswith("```json"): raw = raw[7:]
                if raw.startswith("```"): raw = raw[3:]
                if raw.endswith("```"): raw = raw[:-3]
                    
                payloads = json.loads(raw)
                if isinstance(payloads, list) and len(payloads) > 0 and "username" in payloads[0]:
                    return payloads
                
        except Exception as e:
            print(f"⚠️ AI Generation Failed or Timed Out ({e}). Falling back to hardcoded payloads.")
            return fallback
            
    return fallback
