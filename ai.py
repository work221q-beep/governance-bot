import os
import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL")


async def generate_ai_response(model, prompt, temperature):
    try:
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

            response.raise_for_status()
            data = response.json()
            return data.get("response", "AI error")

    except Exception as e:
        print("OLLAMA ERROR:", str(e))
        return "AI error"


async def get_available_models():
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            data = response.json()

            return [m["name"] for m in data.get("models", [])]

    except Exception as e:
        print("MODEL FETCH ERROR:", str(e))
        return []
