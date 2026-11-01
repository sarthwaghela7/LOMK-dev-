import os

import uvicorn

if __name__ == "__main__":
    # Render sets RENDER=true on every service automatically. reload=True
    # spawns an extra file-watcher process on top of the server process
    # (visible in logs as a separate "Started reloader process" line) --
    # useful for local dev, pure overhead with no benefit in production.
    is_render = bool(os.environ.get("RENDER"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=not is_render,
    )