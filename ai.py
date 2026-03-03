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

            print("Ollama status:", response.status_code)
            print("Ollama raw response:", response.text)

            response.raise_for_status()

            data = response.json()
            return data.get("response", "AI error")

    except Exception as e:
        print("OLLAMA ERROR:", str(e))
        return "AI error"
