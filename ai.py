import os, httpx, asyncio, json, re

OLLAMA_URL = os.getenv("OLLAMA_URL")
# Protect 2vCPU from overload 
arbitration_semaphore = asyncio.Semaphore(2)

async def arbitrate_claim(model, actor, target, claim_text):
    async with arbitration_semaphore:
        try:
            system_prompt = (
                "You are a strict competitive referee. Return ONLY valid JSON. "
                'Format: {"verdict": "valid" or "invalid", "confidence": 0-100}'
            )
            prompt = f"Actor: {actor}\nTarget: {target}\nClaim: {claim_text}"
            
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": model, "prompt": system_prompt + prompt,
                        "temperature": 0.05, "stream": False, "format": "json"
                    }
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                # Clean JSON extraction [cite: 2]
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                parsed = json.loads(match.group())
                return {"verdict": parsed.get("verdict", "invalid"), "confidence": int(parsed.get("confidence", 0))}
        except Exception as e:
            return {"verdict": "invalid", "confidence": 0}
