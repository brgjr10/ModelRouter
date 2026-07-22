import sqlite3

conn = sqlite3.connect(r'F:\Odysseus\odysseus\data\app.db')
conn.row_factory = sqlite3.Row
for r in conn.execute("SELECT id, name, base_url FROM model_endpoints WHERE base_url LIKE '%5000%'").fetchall():
    print(dict(r))
conn.close()
