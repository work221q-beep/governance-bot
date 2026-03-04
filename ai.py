import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL")
# Strictly 1 to protect your 2vCPU AWS VM
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_wave(intensity: int):
    """Generates an array of personas and toxic/scam payloads."""
    async with ai_semaphore:
        try:
            system_prompt = (
                f"You are a red-team AI testing a Discord server. Generate a JSON list of {intensity} "
                "distinct raid payloads. Each must have a 'username' and a 'spam_message' "
                "(e.g., nitro scams, disguised profanity, phishing). "
                "Return ONLY valid JSON: [{'username': 'name', 'spam_message': 'msg'}]"
            )
            
            # 30 second timeout gives your 2vCPU VM plenty of breathing room
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": "llama3", "prompt": system_prompt, "stream": False, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                return json.loads(raw)
        except Exception as e:
            print(f"AI VM Overloaded/Error: {e}")
            return [{"username": "RaidBot", "spam_message": "FREE NITR0 Click Here!"}]
