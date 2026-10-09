import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from koc_auto.feishu import Feishu, FeishuError, TABLE
from koc_auto.model import Observation, count_value, platform_for_url, work_id, source_link
from koc_auto.runner import run


class ModelTests(unittest.TestCase):
    def test_numbers_are_explicit_and_approximate(self):
        self.assertEqual(count_value('0'), (0, None))
        self.assertEqual(count_value('1,109'), (1109, None))
        self.assertEqual(count_value('1.2万'), (12000, '页面缩写，约1.2万'))
        self.assertEqual(count_value('2.1w'), (21000, '页面缩写，约2.1w'))
        for text in ['', '点赞', '转发', '1.1', '-1', '10+', '约10', '10 20']:
            self.assertEqual(count_value(text), (None, None))

    def test_domain_and_identity(self):
        self.assertEqual(platform_for_url('https://xhslink.cn/a'), 'xiaohongshu')
        self.assertIsNone(platform_for_url('https://douyin.com.evil.test/video/123'))
        self.assertIsNone(platform_for_url('https://user:password@douyin.com/video/123'))
        self.assertEqual(work_id('https://www.douyin.com/video/123', 'douyin'), '123')
        self.assertEqual(work_id('https://www.douyin.com/jingxuan?modal_id=123', 'douyin'), '123')
        self.assertIsNone(work_id('https://www.douyin.com/user/abc?modal_id=123', 'douyin'))
        self.assertIsNone(work_id('https://www.douyin.com/user/123', 'douyin'))
        self.assertEqual(source_link([{'type': 'url', 'text': 'https://v.douyin.com/a/'}]), 'https://v.douyin.com/a/')

    def test_beijing_midnight_and_missing_identity(self):
        obs = Observation('douyin', 'https://douyin.com/video/1', work_id='1', account='author', captured_at='2026-10-09T16:01:00+00:00')
        self.assertEqual(obs.daily_key, ('douyin', 'author', '1', '2026-10-10'))
        obs.account = None
        self.assertIsNone(obs.daily_key)
        self.assertIn('未确认', obs.status)


class FakeFeishu(Feishu):
    def __init__(self):
        self.prefix = '/bitable/v1/apps/test'
        self.rows = {'rec1': {'record_id': 'rec1', 'fields': {
            '平台': '抖音', '内容链接': {'link': 'https://www.douyin.com/video/123'},
            '点赞数': 9, '评论数': 7, '收藏数': 8, '转发数': 6, '产品名称': '不改动'}}}
        self.requests = []
    def request(self, method, path, body=None, query=None, auth=True):
        self.requests.append((method, path, body, query))
        if method in ('PUT', 'POST'):
            record = path.split('/')[-1] if method == 'PUT' else 'new1'
            self.rows.setdefault(record, {'record_id': record, 'fields': {}})['fields'].update(body['fields'])
            return {'data': {'record': self.rows[record]}}
        return {'data': {'record': self.rows[path.split('/')[-1]]}}


class FeishuTests(unittest.TestCase):
    def test_unknown_preserved_zero_written_and_readback(self):
        client = FakeFeishu()
        source = json.loads(json.dumps(client.rows['rec1']))
        obs = Observation('douyin', source_link(source['fields']['内容链接']), work_id='123', account='author',
                          values={'likes': 0, 'comments': 84, 'favorites': None, 'shares': None}, outcome='部分成功')
        client.write_source(source, obs)
        fields = client.rows['rec1']['fields']
        self.assertEqual(fields['点赞数'], 0)
        self.assertEqual(fields['评论数'], 84)
        self.assertEqual(fields['收藏数'], 8)
        self.assertEqual(fields['转发数'], 6)
        self.assertEqual(fields['产品名称'], '不改动')
        self.assertEqual([x[0] for x in client.requests], ['GET', 'PUT', 'GET'])

    def test_source_changed_is_not_written(self):
        client = FakeFeishu()
        source = json.loads(json.dumps(client.rows['rec1']))
        client.rows['rec1']['fields']['内容链接'] = {'link': 'https://www.douyin.com/video/456'}
        with self.assertRaises(FeishuError):
            client.write_source(source, Observation('douyin', 'https://www.douyin.com/video/123'))
        self.assertFalse(any(r[0] == 'PUT' for r in client.requests))

    def test_write_readback_mismatch_is_failure(self):
        client = FakeFeishu()
        with patch.object(client, 'get_record', return_value={'fields': {'点赞数': 4}}):
            with self.assertRaises(FeishuError):
                client.verified_write(TABLE, 'rec1', {'点赞数': 3})

    def test_daily_upsert_and_cross_day_history(self):
        client = FakeFeishu()
        rows = []
        obs = Observation('douyin', 'https://www.douyin.com/video/123', work_id='123', account='author',
                          values={'likes': 10}, captured_at='2026-10-09T08:00:00+08:00')
        client.write_history('history', obs, [obs.original_url], rows)
        self.assertEqual(len(rows), 1)
        obs.values['likes'] = 11
        client.write_history('history', obs, [obs.original_url], rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fields']['点赞数'], 11)
        self.assertEqual(client.requests[-2][0], 'PUT')
        obs.captured_at = '2026-10-10T08:00:00+08:00'
        client.write_history('history', obs, [obs.original_url], rows)
        self.assertEqual(len(rows), 2)
        self.assertEqual(client.requests[-2][0], 'POST')

    def test_duplicate_daily_keys_do_not_overwrite(self):
        client = FakeFeishu()
        obs = Observation('douyin', 'https://www.douyin.com/video/123', work_id='123', account='author')
        fields = dict(zip(('平台', '账号', '作品ID', '采集日期'), obs.daily_key))
        with self.assertRaises(FeishuError):
            client.write_history('history', obs, [obs.original_url], [{'record_id': 'a', 'fields': fields}, {'record_id': 'b', 'fields': fields}])
        self.assertEqual(client.requests, [])

    def test_view_id_and_pagination(self):
        client = FakeFeishu()
        answers = [{'data': {'items': [{'record_id': 'a'}], 'has_more': True, 'page_token': 'next'}},
                   {'data': {'items': [{'record_id': 'b'}], 'has_more': False}}]
        with patch.object(client, 'request', side_effect=answers) as request:
            rows = client.records(TABLE, 'view1')
            self.assertEqual(len(rows), 2)
            self.assertEqual(request.call_args.kwargs, {})
            self.assertEqual(request.call_args.args[2]['view_id'], 'view1')
            self.assertEqual(request.call_args.args[3]['page_token'], 'next')


class RunnerTests(unittest.TestCase):
    def test_read_only_cancel_does_not_write_or_save_secrets(self):
        client = FakeFeishu()
        client.inspect = lambda: [client.rows['rec1']]
        with tempfile.TemporaryDirectory() as temp:
            with patch('koc_auto.runner.Feishu', return_value=client), patch('koc_auto.runner.BrowserCollector') as browser, patch('koc_auto.runner.capture_feishu', return_value=[]):
                browser.return_value.__enter__.return_value.resolve.return_value = Observation('douyin', 'https://www.douyin.com/video/123')
                run('test-app', 'SECRET-NOT-SAVED', '', temp, lambda *a: False, lambda *a: None, lambda *a: None, threading.Event())
            self.assertFalse(any(r[0] in ('PUT', 'POST') for r in client.requests))
            self.assertFalse((Path(temp) / '.collector.lock').exists())
            files = list(Path(temp).glob('reports/*/results.json'))
            self.assertEqual(len(files), 1)
            self.assertNotIn('SECRET-NOT-SAVED', files[0].read_text())

    def test_row_failure_does_not_stop_other_rows(self):
        client = FakeFeishu()
        source2 = json.loads(json.dumps(client.rows['rec1']))
        source2['record_id'] = 'rec2'
        source2['fields']['内容链接'] = {'link': 'https://www.douyin.com/video/456'}
        sources = [client.rows['rec1'], source2]
        client.inspect = lambda: sources
        client.prepare_write = lambda: 'history'
        client.records = lambda *a: []
        client.write_source = unittest.mock.Mock(side_effect=[FeishuError('写入失败'), None])
        with tempfile.TemporaryDirectory() as temp:
            with patch('koc_auto.runner.Feishu', return_value=client), patch('koc_auto.runner.BrowserCollector') as browser, patch('koc_auto.runner.capture_feishu', return_value=[]):
                browser.return_value.__enter__.return_value.resolve.side_effect = [
                    Observation('douyin', source_link(s['fields']['内容链接']), values={'likes': 1}, outcome='部分成功') for s in sources]
                run('app', 'secret', '', temp, lambda *a: True, lambda *a: None, lambda *a: None, threading.Event())
            self.assertEqual(client.write_source.call_count, 2)
            summary = json.loads(next(Path(temp).glob('reports/*/summary.json')).read_text())
            self.assertEqual(summary['总行数'], 2)
            self.assertEqual(summary['失败行数'], 1)
            self.assertEqual(summary['部分成功行数'], 1)


if __name__ == '__main__':
    unittest.main()
