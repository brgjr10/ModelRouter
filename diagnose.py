import sqlite3
import httpx
import json

# Check DB
conn = sqlite3.connect(r'F:\Odysseus\odysseus\data\app.db')
conn.row_factory = sqlite3.Row
for r in conn.execute("SELECT id, name, base_url, api_key FROM model_endpoints WHERE is_enabled=1").fetchall():
    print(f"{r['id']} | {r['name']} | {r['base_url']} | key={r['api_key'][:20] if r['api_key'] else 'None'}...")
conn.close()

# Test each upstream
paths = [
    ("local llama.cpp", "http://192.168.86.241:5050/v1/chat/completions", {"x-api-key": "<API_KEY>"}),
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions", {"Authorization": "Bearer sk-or-v1-test"}),
]

for name, url, headers in paths:
    try:
        payload = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        r = httpx.post(url, json=payload, headers=headers, timeout=10)
        print(f"{name}: {r.status_code}")
    except Exception as e:
        print(f"{name}: ERROR {str(e)[:80]}")
