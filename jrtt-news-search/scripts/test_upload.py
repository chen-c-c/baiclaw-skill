"""Quick test: upload one article to the backend API."""
import json, sqlite3, requests, sys
from pathlib import Path

# 1. Read token, deviceId, testMode from DB
db_paths = [
    r'C:\Users\EDY\AppData\Roaming\BaiClaw\baiclaw.sqlite',
    r'D:\workspace-ywxs\BaiClaw\baiclaw.sqlite',
]
db_path = None
for p in db_paths:
    if Path(p).exists():
        db_path = Path(p)
        break

if not db_path:
    print('DB not found')
    sys.exit(1)

print(f'Using DB: {db_path}')
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

token = None
for key in ('deviceToken', 'auth.saToken', 'auth.token'):
    row = cur.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
    if row and row[0]:
        token = row[0]
        print(f'Token from "{key}": {token[:10]}...{token[-4:]}')
        break

row = cur.execute('SELECT value FROM kv WHERE key=?', ('deviceId',)).fetchone()
device_id = row[0] if row else ''
print(f'deviceId: {device_id}')

row = cur.execute('SELECT value FROM kv WHERE key=?', ('app_config',)).fetchone()
app_config = json.loads(row[0]) if row and row[0] else {}
test_mode = app_config.get('app', {}).get('testMode', False)
print(f'testMode: {test_mode}')

conn.close()

if not token or not device_id:
    print('Missing token or deviceId, aborting')
    sys.exit(1)

# 2. Build URL (v3 for test mode, matching endpoints.ts)
api_version = 'v3' if test_mode else 'v2'
api_url = f'https://ai.yuanweixiansi.com/api/{api_version}/device/article/save'
print(f'API URL: {api_url}')

# 3. Read article file
article_path = Path(r'D:\workspace-ywxs\BaiClaw\SKILLs\jrtt-news-search\jrtt\test_upload\AI时代，更需保障劳动权益.md')
content = article_path.read_text(encoding='utf-8')

# Parse article fields
title = ''
publish_time = ''
author = ''
url = ''
body_lines = []
in_body = False

for line in content.split('\n'):
    if line.startswith('# ') and not title:
        title = line[2:].strip()
    elif line.startswith('- **发布时间**: '):
        t = line.replace('- **发布时间**: ', '').strip()
        if t and t != '未知':
            publish_time = t
    elif line.startswith('- **作者**: '):
        author = line.replace('- **作者**: ', '').strip()
    elif line.startswith('- **原文链接**: '):
        url = line.replace('- **原文链接**: ', '').strip()
    elif line.startswith('---') and not in_body:
        in_body = True
        continue
    elif in_body:
        body_lines.append(line)

body_text = '\n'.join(body_lines).strip()

print(f'\nTitle: {title}')
print(f'Author: {author}')
print(f'PublishTime: "{publish_time}"')
print(f'Body length: {len(body_text)} chars')
print(f'URL: {url}')

# 4. Call API
payload = {
    'title': title,
    'content': body_text,
    'publishDate': publish_time,
    'url': url,
    'publisher': author,
    'articleSource': 'toutiao',
}

headers = {
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/json',
}

print(f'\n>>> POST {api_url}')
try:
    resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
    print(f'<<< HTTP {resp.status_code}')
    print(f'<<< Body: {resp.text[:1000]}')

    if resp.status_code == 200:
        body = resp.json()
        if body.get('code') == 200:
            print('\n=== SUCCESS! Article uploaded. ===')
        else:
            print(f'\nAPI error: code={body.get("code")}, message={body.get("message", "N/A")}')
    elif resp.status_code == 401:
        print('\n=== 401 Unauthorized ===')
        # Try v2 as fallback
        v2_url = api_url.replace('/api/v3/', '/api/v2/')
        print(f'\nTrying v2 fallback: {v2_url}')
        resp2 = requests.post(v2_url, json=payload, headers=headers, timeout=30)
        print(f'<<< HTTP {resp2.status_code}')
        print(f'<<< Body: {resp2.text[:1000]}')
except Exception as e:
    print(f'Request failed: {e}')
