from datetime import datetime
from pathlib import Path
import os
import shutil
import sqlite3

from database import BANCO as DB

# Backups ficam no mesmo Volume persistente quando a SmokePay roda na Railway.
volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DEST = (Path(volume) if volume else DB.parent) / "backups"
DEST.mkdir(parents=True, exist_ok=True)

if not DB.exists():
    raise SystemExit(f"Banco nao encontrado: {DB}")

conn = sqlite3.connect(DB)
try:
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SystemExit("Banco falhou no integrity check; backup cancelado")
finally:
    conn.close()

nome = DEST / f"smokepay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
shutil.copy2(DB, nome)
print(f"Backup criado: {nome}")
