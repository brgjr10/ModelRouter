# ModelRouter

High-performance LLM model proxy and API router with multi-provider support.

## Stack

- **Backend**: Python / FastAPI / uvicorn
- **Frontend**: JavaScript scaffold

## Features

- Multi-provider routing
- Health checks and capacity scoring
- Configurable provider list via `providers.json`
- API key management per provider

## Running

```bash
pip install -r requirements.txt
uvicorn proxy:app --host 0.0.0.0 --port 8000
```

## Config

Edit `providers.json` to add your providers and API keys. Do not commit real keys to version control.
