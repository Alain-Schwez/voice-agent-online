from fastapi import FastAPI
import os

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok", "port": os.getenv("PORT")}
