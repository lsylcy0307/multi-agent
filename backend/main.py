from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(payload: dict):
    return {
        "message": "Mock response for now",
        "input": payload
    }