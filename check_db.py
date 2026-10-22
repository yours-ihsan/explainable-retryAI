import sqlite3

conn = sqlite3.connect("retry_history.db")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", tables)
for (name,) in tables:
    print(name, "->", conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0], "rows")
conn.close()