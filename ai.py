import os
import httpx
import asyncio
import json
import re

OLLAMA_URL = os.getenv("OLLAMA_URL")

ARBITRATION_LIMIT = 2
arbitration_semaphore = asyncio.Semaphore(ARBITRATION_LIMIT)


async def arbitrate_claim(actor, target, claim_text):
    async with arbitration_semaphore:
        try:
            system_prompt = (
                "You are a strict competitive referee.\n"
                "Return ONLY valid JSON.\n"
                'Format: {"verdict": "valid" or "invalid", "confidence": 0-100}\n'
                "Do not explain."
            )

            prompt = f"""
Actor: {actor}
Target: {target}
Claim: {claim_text}
"""

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": "phi3:mini",
                        "prompt": system_prompt + prompt,
                        "temperature": 0.05,
                        "stream": False,
                        "options": {"num_predict": 80}
                    }
                )

                response.raise_for_status()
                raw = response.json().get("response", "").strip()

                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not match:
                    raise ValueError("No JSON found")

                parsed = json.loads(match.group())

                return {
                    "verdict": parsed.get("verdict", "invalid"),
                    "confidence": int(parsed.get("confidence", 0))
                }

        except Exception as e:
            print("ARBITRATION ERROR:", str(e))
            return {"verdict": "invalid", "confidence": 0}
