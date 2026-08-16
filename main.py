from fastapi import FastAPI

app = FastAPI(title="fagolab-api")


@app.get("/health")
def health():
    return {"status": "ok", "app": "fagolab-api"}


@app.get("/")
def root():
    return {"message": "fagolab-api is running"}