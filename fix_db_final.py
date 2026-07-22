import sqlite3

conn = sqlite3.connect(r'F:\Odysseus\odysseus\data\app.db')
conn.row_factory = sqlite3.Row

print("=== BEFORE ===")
for r in conn.execute("SELECT id, name, base_url FROM model_endpoints WHERE base_url LIKE '%5000%' OR base_url LIKE '%192.168.86.241%'").fetchall():
    print(r['id'], r['name'], r['base_url'])

# Delete ALL self-loops (anything pointing to port 5000)
conn.execute("DELETE FROM model_endpoints WHERE base_url LIKE '%:5000%'")
print("\n=== DELETED self-loops ===")

# Fix llama.cpp key to plaintext
conn.execute("UPDATE model_endpoints SET api_key='<API_KEY>' WHERE id='84e5cdcf'")
print("Fixed llama.cpp key")

conn.commit()

print("\n=== AFTER ===")
for r in conn.execute("SELECT id, name, base_url, api_key FROM model_endpoints WHERE base_url LIKE '%192.168.86.241%'").fetchall():
    print(r['id'], r['name'], r['base_url'], repr(r['api_key'][:30] + '...'))

conn.close()
