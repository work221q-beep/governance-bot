import os
import httpx
import asyncio

OLLAMA_URL = os.getenv("OLLAMA_URL")

generation_lock = asyncio.Lock()


async def generate_ai_response(model, prompt, temperature):
    async with generation_lock:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "temperature": temperature,
                        "stream": False,
                        "options": {
                            "num_predict": 120,      # 🔥 LIMIT TOKENS
                            "num_ctx": 2048,         # 🔥 SMALLER CONTEXT
                            "top_k": 40,
                            "top_p": 0.9
                        }
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
