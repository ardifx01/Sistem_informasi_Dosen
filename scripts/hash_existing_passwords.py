# scripts/hash_existing_passwords.py
import os
import sqlite3
from werkzeug.security import generate_password_hash

DB_PATH = os.getenv("DATABASE", "database.db")

def is_hashed(pw: str) -> bool:
    """Cek apakah string sudah berupa hash werkzeug."""
    if not isinstance(pw, str):
        return False
    # Werkzeug default: "pbkdf2:sha256:..." atau "scrypt:..."
    return pw.startswith("pbkdf2:") or pw.startswith("scrypt:")

def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"❌ Database file not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT rowid, nip, password FROM users")
    rows = cur.fetchall()

    to_update = []
    for r in rows:
        pw = r["password"]

        # SQLite bisa simpan angka sebagai int, ubah ke str dulu
        if isinstance(pw, (int, float)):
            pw = str(pw)

        if not is_hashed(pw):
            to_update.append((r["rowid"], r["nip"], pw))

    print(f"🔍 Found {len(to_update)} plaintext password(s).")

    updated = 0
    for rowid, nip, pw in to_update:
        hashed = generate_password_hash(str(pw))
        cur.execute("UPDATE users SET password = ? WHERE rowid = ?", (hashed, rowid))
        print(f"✅ Updated NIP {nip}")
        updated += 1

    conn.commit()
    conn.close()
    print(f"🎉 Done! Updated {updated} password(s) to hashed form.")

if __name__ == "__main__":
    main()
