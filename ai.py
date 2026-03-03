import os
import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL")

async def generate_ai_response(model, prompt, temperature):
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "temperature": temperature,
                "stream": False
            }
        )
        data = response.json()
        return data.get("response", "AI error")
