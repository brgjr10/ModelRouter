import httpx
import json

payload = {
    "model": "ModelRouter",
    "messages": [{"role": "user", "content": "Reply with the single word: alive"}],
    "stream": False
}
headers = {"Content-Type": "application/json"}
url = "http://localhost:5000/v1/chat/completions"

print("POST", url)
try:
    r = httpx.post(url, json=payload, headers=headers, timeout=60)
    print("status:", r.status_code)
    body = r.json()
    print("model field:", body.get("model"))
    choices = body.get("choices", [])
    print("choices count:", len(choices))
    if choices:
        msg = choices[0].get("message", {})
        print("role:", msg.get("role"))
        print("content:", repr(msg.get("content", ""))[:200])
    else:
        print("no choices, body keys:", list(body.keys()))
        print("raw:", str(body)[:300])
except Exception as e:
    print("ERROR:", repr(e))
