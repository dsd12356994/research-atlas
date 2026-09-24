"""Use SQLite's online backup API instead of copying a live WAL database."""
from pathlib import Path
from datetime import datetime
import sqlite3
root=Path(__file__).resolve().parents[1]
destination=root/'backups';destination.mkdir(exist_ok=True)
target=destination/(datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite')
src=sqlite3.connect(root/'data'/'research.db');dst=sqlite3.connect(target)
with dst:
    src.backup(dst)
dst.close();src.close()
print(target)
