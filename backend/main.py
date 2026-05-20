from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from rag_search import search_grants

app = FastAPI(title="Nonprofit Grant Search API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    state: str | None = None
    grant_size: str | None = None
    open_only: bool = False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
def search(request: SearchRequest):
    try:
        return search_grants(
            query=request.query,
            top_k=request.top_k,
            state=request.state,
            grant_size=request.grant_size,
            open_only=request.open_only,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))