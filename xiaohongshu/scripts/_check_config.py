#!/usr/bin/env python3
import json, os, sqlite3
db = os.path.join(os.environ['APPDATA'], 'BaiClaw', 'baiclaw.sqlite')
if os.path.exists(db):
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    row = conn.execute("SELECT value FROM kv WHERE key='app_config'").fetchone()
    if row:
        config = json.loads(row[0])
        print('testMode:', config.get('app', {}).get('testMode'))
        print('BAICLAW_ADMIN_API_URL env:', os.environ.get('BAICLAW_ADMIN_API_URL', '(not set)'))
        print('BAICLAW_ADMIN_TOKEN env:', 'set' if os.environ.get('BAICLAW_ADMIN_TOKEN') else '(not set)')
        if config.get('app', {}).get('testMode'):
            print('=> Script will try localhost:8081 (test mode)')
        else:
            print('=> Script will try production API')
    else:
        print('No app_config row found')
    conn.close()
else:
    print('DB not found at', db)
