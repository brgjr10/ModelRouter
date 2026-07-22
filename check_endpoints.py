import sqlite3

conn = sqlite3.connect(r'F:\Odysseus\odysseus\data\app.db')
conn.row_factory = sqlite3.Row

for r in conn.execute("SELECT id, name, base_url, api_key FROM model_endpoints WHERE base_url LIKE '%5000%' OR base_url LIKE '%192.168.86.241%'").fetchall():
    print(dict(r))

conn.close()
