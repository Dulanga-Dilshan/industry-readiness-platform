from fastapi import FastAPI

app = FastAPI(title="backend")


@app.get("/check")
async def check():
    return {"status": "ok"}
