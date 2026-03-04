import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL")
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_wave(intensity: int, primary_model="llama3"):
    """Generates an array of personas with a cascading model waterfall."""
    
    # Cascade order: User Preferred -> Mistral -> Phi3 -> Fallback
    models_to_try = [primary_model, "mistral", "phi3:mini"]
    
    fallback_payloads = [
        {"username": f"Ghost_{i}", "spam_message": "🚨 SECURITY TEST: Automated Phishing Vector."} 
        for i in range(intensity)
    ]
    
    async with ai_semaphore:
        for model in models_to_try:
            try:
                system_prompt = (
                    f"Generate a JSON array of {intensity} objects. "
                    "Keys MUST be 'username' and 'spam_message' (Discord phishing scam). "
                    "Output ONLY raw JSON."
                )
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{OLLAMA_URL}/api/generate",
                        json={"model": model, "prompt": system_prompt, "stream": False, "format": "json"}
                    )
                    response.raise_for_status()
                    raw = response.json().get("response", "").strip()
                    
                    if raw.startswith("
http://googleusercontent.com/immersive_entry_chip/0
http://googleusercontent.com/immersive_entry_chip/1

---

### 📝 Configuration Table
| Feature | Implementation | Purpose |
| :--- | :--- | :--- |
| **Model Waterfall** | Recursive `try/except` in `ai.py` | Ensures payload delivery if primary model fails. |
| **Sandbox Module** | `!setup_sandbox` in `bot.py` | Prevents testing from leaking into public channels[cite: 2]. |
| **Role Scanner** | Web DB + Discord Perm Check | Identifies if "Member" roles have "Manage Server" perms. |
| **Auto-Cleanup** | Async `delete()` loop | Maintains server professional appearance post-test[cite: 2]. |

Would you like me to generate the specific **Role Permission Scanner** logic for the bot to check for "Dangerous Permissions" (like `Administrator` or `Manage Webhooks`) on the IDs saved in your dashboard?
