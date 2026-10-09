import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app import create_server

class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.start()
    def start(self):
        self.server = create_server('127.0.0.1', 0, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
    def tearDown(self):
        self.stop()
        self.temp.cleanup()
    def request(self, path, method='GET', data=None):
        req = Request(self.base + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type': 'application/json'})
        with urlopen(req) as response:
            return response.status, response.read()
    def record(self):
        return dict(platform='douyin', url='https://v.douyin.com/example/', title='测试作品', author='作者', notes='', likes=0, comments=12, favorites=None, shares=3)
    def test_crud_export_and_restart(self):
        record_id = json.loads(self.request('/api/records', 'POST', self.record())[1])['id']
        self.stop()
        self.start()
        row = json.loads(self.request('/api/records')[1])[0]
        self.assertEqual(row['likes'], 0)
        self.assertIsNone(row['favorites'])
        updated = self.record()
        updated.update(likes=99, notes='=HYPERLINK("bad")')
        self.request(f'/api/records/{record_id}', 'PUT', updated)
        exported = list(csv.reader(io.StringIO(self.request('/api/export')[1].decode('utf-8-sig'))))
        self.assertEqual(exported[1][4:8], ['99', '12', '', '3'])
        self.assertTrue(exported[1][8].startswith("'="))
        self.request(f'/api/records/{record_id}', 'DELETE')
        self.assertEqual(json.loads(self.request('/api/records')[1]), [])
    def test_validation(self):
        for key, value in [('likes', -1), ('comments', 1.5), ('shares', True), ('url', 'https://douyin.com.evil.test/a'), ('url', 'javascript:alert(1)')]:
            data = self.record()
            data[key] = value
            with self.assertRaises(HTTPError) as error:
                self.request('/api/records', 'POST', data)
            self.assertEqual(error.exception.code, 400)
        data = self.record()
        data.update(platform='xiaohongshu', url='https://xhslink.com/example')
        self.assertEqual(self.request('/api/records', 'POST', data)[0], 201)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/records/999', 'DELETE')
        self.assertEqual(error.exception.code, 404)
    def test_assets(self):
        for path, fragment in [('/', 'KOC 数据记录'), ('/app.js', 'fetch('), ('/style.css', '.layout')]:
            status, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(fragment, body.decode())

if __name__ == '__main__':
    unittest.main()
