#!/bin/bash
# Ежедневный бэкап SQLite через контейнер (корректно при WAL), ротация 30 копий
docker exec topic-search-bot python3 -c "
import sqlite3, datetime, pathlib
pathlib.Path('/data/backups').mkdir(exist_ok=True)
name = f'/data/backups/topic_search_{datetime.datetime.now():%Y%m%d_%H%M}.db'
src = sqlite3.connect('/data/topic_search.db')
dst = sqlite3.connect(name)
src.backup(dst)
dst.close(); src.close()
files = sorted(pathlib.Path('/data/backups').glob('*.db'), reverse=True)
for f in files[30:]:
    f.unlink()
print('backup ok:', name)
"
