import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL")
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_wave(intensity: int):
    async with ai_semaphore:
        try:
            system_prompt = (
                f"Generate a JSON array of {intensity} objects. "
                "Keys: 'username' (hacker names), 'spam_message' (short discord scams/phishing). "
                "Output ONLY valid JSON."
            )
            
            # Increased to 60s so your 2vCPU AWS VM doesn't choke
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": "llama3", "prompt": system_prompt, "stream": False, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                # Clean up markdown if the AI adds it
                if raw.startswith("```json"): raw = raw[7:-3]
                return json.loads(raw)
        except Exception as e:
            print(f"AI VM Overloaded/Error: {e}")
            # Robust fallback so the test continues even if AI fails
            return [{"username": f"RaidBot_{i}", "spam_message": "FREE NITR0! Click Here: [http://phish.gg](http://phish.gg)"} for i in range(intensity)]
