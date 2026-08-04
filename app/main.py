from fastapi import FastAPI

app = FastAPI(
    title="Sentinel Agent Server",
    description="Multi-Agent AI Financial Analyst",
    version="1.0.0"
)

@app.get("/")
def read_root():
    return {"message": "Sentinel Agent Server is running"}
