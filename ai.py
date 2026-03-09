import os, json, random, asyncio
import httpx
import datetime
from db import payload_armory
from crypto import encrypt_data

SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

MODULES = [
    "phishing", "spam_flood", "fake_mod", "insider_threat", "escalation", "harassment"
]

# Set caps to 30 per module, per alignment (Innocent / Malicious) = Total 360 capacity
CAPS = {m: 30 for m in MODULES}
for m in MODULES:
    CAPS[f"innocent_{m}"] = 30

def get_system_prompt(raid_type: str, alignment: str) -> str:
    topics = ["crypto trading", "server drama", "gaming patch notes", "nitro giveaways", "bot development", "discord updates"]
    topic = random.choice(topics)
    
    if alignment == "innocent":
        return f"""You are a regular Discord user chatting about {topic}.
Your goal is to simulate normal, non-malicious conversation that MIGHT casually mention words related to {raid_type}, but is completely safe.
Output ONLY a raw JSON array of 5 strings. No markdown, no formatting, no introduction.
Example: ["Hey guys, did you see the new update?", "I think the server rules changed.", "Anyone want to play ranked?", "I'm having login issues.", "That meme is hilarious!"]"""
    else:
        return f"""You are simulating a malicious Discord actor executing a {raid_type} attack. Context: {topic}.
Output ONLY a raw JSON array of 5 highly realistic, malicious strings that a moderator would need to catch.
No markdown, no formatting, no introduction, no code blocks. Just the raw JSON array of strings."""

async def harvest_payloads(raid_type: str, alignment: str = "malicious"):
    target_type = raid_type if alignment == "malicious" else f"innocent_{raid_type}"
    
    try:
        current_count = await payload_armory.count_documents({"raid_type": target_type})
        if current_count >= CAPS.get(target_type, 30):
            return

        system_prompt = get_system_prompt(raid_type, alignment)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.sambanova.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "Meta-Llama-3.1-8B-Instruct",
                    "messages": [{"role": "system", "content": system_prompt}],
                    "temperature": 0.7, # Boosted creativity to stop repetitive bot-like messages
                    "top_p": 0.9
                },
                timeout=20.0
            )
            
            if response.status_code != 200:
                print(f"[AI Error] SambaNova API returned {response.status_code}: {response.text}")
                return

            data = response.json()
            raw_text = data["choices"][0]["message"]["content"].strip()
            
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            try:
                payloads = json.loads(raw_text)
            except json.JSONDecodeError as e:
                print(f"[AI Error] Failed to parse JSON for {target_type}: {e}")
                return

            if not isinstance(payloads, list):
                print(f"[AI Error] Output was not a list for {target_type}")
                return

            docs = []
            import uuid
            for msg in payloads[:5]:
                if not isinstance(msg, str): continue
                
                # Randomize usernames to make simulations realistic
                prefix = random.choice(["Ghost", "Shadow", "Cyber", "Neon", "Void", "Zero", "Nova"])
                suffix = random.randint(1000, 9999)
                fake_user = f"{prefix}User{suffix}"
                
                # Encrypt data before storing to protect memory & database constraints
                enc_user = encrypt_data(fake_user)
                enc_msg = encrypt_data(msg)
                
                docs.append({
                    "payload_id": str(uuid.uuid4()),
                    "raid_type": target_type,
                    "username": enc_user,
                    "spam_message": enc_msg,
                    "created_at": datetime.datetime.utcnow()
                })

            if docs:
                await payload_armory.insert_many(docs)
                print(f"⚡ Harvester generated {len(docs)} {target_type} payloads.")

    except httpx.ReadTimeout:
        print(f"[AI Timeout] SambaNova API timed out while generating {target_type}.")
    except Exception as e:
        # PERMANENT FIX: No more silent failures. All exceptions are logged.
        print(f"[AI Harvester Critical Error] Failed on {target_type}: {str(e)}")

async def parallel_harvest_sweep():
    """Generates payloads while respecting 0.5 vCPU hardware limits."""
    # SEMAPHORE: Strictly limits concurrency to 2 parallel tasks so the CPU doesn't melt.
    sem = asyncio.Semaphore(2)
    
    async def bounded_harvest(rt, align):
        async with sem:
            await harvest_payloads(rt, align)
            await asyncio.sleep(2) # Breather between API calls

    tasks = []
    for mod in MODULES:
        tasks.append(bounded_harvest(mod, "malicious"))
        tasks.append(bounded_harvest(mod, "innocent"))
        
    await asyncio.gather(*tasks)

async def harvest_loop():
    """Continuous background daemon that keeps the Armory full."""
    await asyncio.sleep(5)
    while True:
        try:
            await parallel_harvest_sweep()
        except Exception as e:
            print(f"[AI Loop Error] Sweep failed: {e}")
        # HARDWARE OPTIMIZATION: Wait 15 seconds before checking again. 
        # Drops CPU idle usage to virtually 0%.
        await asyncio.sleep(15)
