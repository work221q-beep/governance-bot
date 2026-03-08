import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

# Import Discord Bot and Background Tasks
from bot import bot, TOKEN, sweep_spam_tracker

# Import Modular Routers
from routers.auth import auth_router
from routers.admin import admin_router
from routers.dashboard import dashboard_router
from routers.server import server_router

# Initialize the main FastAPI application
app = FastAPI(title="Sylas Governance Bot API", version="2.0.0")

# Mount Static Files Directory (Environment-Resilient)
import os
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize Templates
templates = Jinja2Templates(directory="templates")

# Register ALL Routers securely
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(server_router)

@app.on_event("startup")
async def startup_event():
    """Starts the Discord Bot and Background Services alongside the Web API."""
    print("[SYSTEM] Booting Sylas Governance Web Matrix...")
    
    # Start the Discord Bot Connection
    asyncio.create_task(bot.start(TOKEN))
    
    # Start the Secure Memory Sweep (DoS Protection Task)
    asyncio.create_task(sweep_spam_tracker())
    
    print("[SYSTEM] All Routers mounted. Gateway connected.")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Landing Page"""
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    # Start the server locally for testing
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)