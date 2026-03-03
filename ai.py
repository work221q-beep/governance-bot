import os
import httpx
import asyncio

OLLAMA_URL = os.getenv("OLLAMA_URL")

# Prevent concurrent generations (important for 2 vCPU)
generation_lock = asyncio.Lock()


async def generate_ai_response(model, prompt, temperature):
    async with generation_lock:
        try:
            # System identity and strict behavior control
            system_prompt = (
                "You are Sylas, a Discord AI assistant. "
                "Reply clearly and concisely. "
                "Do not switch languages unless the user does. "
                "Do not generate instructions, tasks, or unrelated content. "
                "Do not simulate conversations. "
                "Only respond directly to the user's message."
            )

            # Structured prompt format
            full_prompt = f"{system_prompt}\n\nUser: {prompt}\nSylas:"

            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": model,
                        "prompt": full_prompt,
                        "temperature": float(temperature),
                        "stream": False,
                        "options": {
                            "num_predict": 120,   # Limits response length (speed boost)
                            "num_ctx": 2048,      # Smaller context for faster CPU inference
                            "top_k": 40,
                            "top_p": 0.9,
                            "stop": [
                                "User:",
                                "Sylas:",
                                "---"
                            ]
                        }
                    }
                )

                response.raise_for_status()
                data = response.json()

                return data.get("response", "AI error").strip()

        except Exception as e:
            print("OLLAMA ERROR:", str(e))
            return "AI error"


async def get_available_models():
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            data = response.json()

            return [model["name"] for model in data.get("models", [])]

    except Exception as e:
        print("MODEL FETCH ERROR:", str(e))
        return []
