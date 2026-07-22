import sys, json, threading, time
sys.path.insert(0, r'F:\ModelRouter')
from proxy import registry, app
import httpx

registry.sync()
flat = registry.get_flat()
print('flat count:', len(flat))
for m in flat[:20]:
    print(m['model_id'], '|', m['endpoint_name'], '|', m['chat_url'])

# probe a chat with explicit upstream
payload = {
    'model': 'ModelRouter',
    'messages': [{'role': 'user', 'content': 'Say the word working'}],
}
headers = {'Content-Type': 'application/json'}
url = 'http://localhost:5000/v1/chat/completions'
print('\nPOST', url)
try:
    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)) as c:
        r = c.post(url, json=payload, headers=headers)
        print('status', r.status_code)
        print(r.text[:300])
except Exception as e:
    print('FAIL', repr(e))
