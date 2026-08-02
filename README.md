# ModelRouter

High-performance LLM model proxy and API router with multi-provider support.

<img width="1594" height="717" alt="image" src="https://github.com/user-attachments/assets/0eb34642-4ecf-4235-9bbb-f35bf1774e3e" />

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
