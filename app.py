"""Local metrics notebook. Run with Python 3.10+; no external dependencies."""
import argparse
import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
METRICS = ('likes', 'comments', 'favorites', 'shares')


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('记录格式不正确')
    platform = data.get('platform')
    if platform not in ('douyin', 'xiaohongshu'):
        raise ValueError('请选择抖音或小红书')
    url = str(data.get('url', '')).strip()
    parsed = urlsplit(url)
    domains = ('douyin.com', 'iesdouyin.com') if platform == 'douyin' else ('xiaohongshu.com', 'xhslink.com')
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or not any(parsed.hostname == d or parsed.hostname.endswith('.' + d) for d in domains)):
        raise ValueError('请填写对应平台的完整作品链接（以 https:// 开头）')
    result = {'platform': platform, 'url': url}
    for key in ('title', 'author', 'notes'):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError('文字过长或格式不正确')
        result[key] = value.strip()
    for key in METRICS:
        value = data.get(key)
        if value is None or value == '':
            result[key] = None
        elif isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9007199254740991:
            raise ValueError('数量必须为非负整数，不知道的数量请留空')
        else:
            result[key] = value
    result['recorded_at'] = datetime.now(timezone.utc).isoformat()
    return result


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY, platform TEXT NOT NULL, url TEXT NOT NULL,
            title TEXT NOT NULL, author TEXT NOT NULL, notes TEXT NOT NULL,
            likes INTEGER, comments INTEGER, favorites INTEGER, shares INTEGER,
            recorded_at TEXT NOT NULL)''')


def csv_export(records):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['平台', '作品标题', '作者', '作品链接', '点赞数', '评论数', '收藏数', '转发数', '备注', '记录时间（UTC）'])
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + value
        return value
    for row in records:
        writer.writerow([safe(v) for v in [
            '抖音' if row['platform'] == 'douyin' else '小红书', row['title'], row['author'], row['url'],
            *[row[k] for k in METRICS], row['notes'], row['recorded_at']]])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if content_type.startswith('text/csv'):
            self.send_header('Content-Disposition', 'attachment; filename="koc-data.csv"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/api/records', '/api/export'):
            with connect(self.server.db_path) as db:
                records = [dict(r) for r in db.execute('SELECT * FROM records ORDER BY id DESC')]
            self.send(200, csv_export(records), 'text/csv; charset=utf-8') if path.endswith('export') else self.send(200, records)
        elif path in ('/', '/app.js', '/style.css'):
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
            mime = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
            self.send(200, (ROOT / 'static' / name).read_bytes(), mime + '; charset=utf-8')
        else:
            self.send(404, {'error': '页面不存在'})

    def mutate(self, method):
        try:
            path = urlsplit(self.path).path
            if path != '/api/records' and not path.startswith('/api/records/'):
                return self.send(404, {'error': '接口不存在'})
            record_id = None if path == '/api/records' else int(path.removeprefix('/api/records/'))
            if (method == 'POST' and record_id is not None) or (method != 'POST' and record_id is None):
                return self.send(405, {'error': '操作不支持'})
            if method != 'DELETE':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32768:
                    raise ValueError('请求大小不正确')
                data = validate(json.loads(self.rfile.read(length)))
            with connect(self.server.db_path) as db:
                if method == 'POST':
                    keys = list(data)
                    cursor = db.execute(f'INSERT INTO records ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})', list(data.values()))
                    record_id = cursor.lastrowid
                else:
                    if not db.execute('SELECT id FROM records WHERE id=?', (record_id,)).fetchone():
                        return self.send(404, {'error': '记录不存在'})
                    if method == 'DELETE':
                        db.execute('DELETE FROM records WHERE id=?', (record_id,))
                    else:
                        db.execute('UPDATE records SET ' + ','.join(k + '=?' for k in data) + ' WHERE id=?', [*data.values(), record_id])
            self.send(201 if method == 'POST' else 200, {'id': record_id})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send(400, {'error': str(exc) or '输入格式不正确'})

    def do_POST(self):
        self.mutate('POST')

    def do_PUT(self):
        self.mutate('PUT')

    def do_DELETE(self):
        self.mutate('DELETE')


def create_server(host, port, db_path):
    initialize(db_path)
    server = ThreadingHTTPServer((host, port), Handler)
    server.db_path = str(db_path)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', default=os.environ.get('KOC_DB_PATH', str(ROOT / '.data' / 'records.sqlite3')))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.db)
    print(f'服务已启动，端口 {args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
