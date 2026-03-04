import os, httpx, asyncio, json

OLLAMA_URL = os.getenv("OLLAMA_URL")
ai_semaphore = asyncio.Semaphore(1) 

async def generate_raid_wave(intensity: int):
    async with ai_semaphore:
        try:
            system_prompt = (
                f"Generate a JSON array of {intensity} objects. "
                "Keys: 'username' (hacker handle), 'spam_message' (Discord phishing scam). "
                "Output ONLY valid JSON."
            )
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": "llama3", "prompt": system_prompt, "stream": False, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("response", "").strip()
                if raw.startswith("
http://googleusercontent.com/immersive_entry_chip/0

### 5. `templates/leaderboard.html` (The Clean Checklist)
This UI shows a professional security checklist.

```html
<div class="bg-[#181a20] p-6 rounded-xl border border-zinc-800 shadow-lg">
    <h3 class="font-bold mb-6 uppercase text-zinc-400 border-b border-zinc-800 pb-2">Active Vulnerability Map</h3>
    
    {% if vulns %}
        <div class="space-y-3">
        {% for v in vulns %}
            <div class="flex justify-between items-center bg-[#0f1115] p-4 rounded-lg border {% if v.status == 'SECURE' %}border-emerald-900/30{% else %}border-red-900/50 bg-red-900/5{% endif %}">
                <div>
                    <span class="font-bold text-sm tracking-wide {{ 'text-emerald-500' if v.status == 'SECURE' else 'text-red-500 animate-pulse' }}">
                        [{{ v.status }}]
                    </span>
                    <span class="ml-2 font-mono text-white">{{ v.vuln_name }}</span>
                    <p class="text-sm text-zinc-400 mt-1">{{ v.details }}</p>
                </div>
                <span class="text-xs text-zinc-600 font-mono">Last Scan: {{ v.last_tested.strftime('%Y-%m-%d %H:%M') }} UTC</span>
            </div>
        {% endfor %}
        </div>
    {% else %}
        <p class="text-zinc-600 italic text-center py-10">No vulnerability data. Run !startraid to map the server.</p>
    {% endif %}
</div>
