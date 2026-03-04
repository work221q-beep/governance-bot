import os, httpx, asyncio, json, re

OLLAMA_URL = os.getenv("OLLAMA_URL")
arbitration_semaphore = asyncio.Semaphore(2)

async def arbitrate_claim(model, actor, target, claim_text):
    async with arbitration_semaphore:
        try:
            system_prompt = (
                "You are a gaming referee for a competitive Discord server. "
                "Evaluate if the user's claim is standard gaming banter or a victory claim (e.g., 'I beat you', 'I won', 'smoked you'). "
                "If it is a normal gaming trash talk claim, return 'valid'. If it is complete gibberish or wildly impossible, return 'invalid'. "
                'Return ONLY valid JSON: {"verdict": "valid" | "invalid", "confidence": 0-100}'
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
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                parsed = json.loads(match.group())
                return {"verdict": parsed.get("verdict", "invalid"), "confidence": int(parsed.get("confidence", 0))}
        except Exception as e:
            print(f"AI Arbitration Error: {e}") 
            # Returns an error state to protect player stats
            return {"verdict": "error", "confidence": 0}
