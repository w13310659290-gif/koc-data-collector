"""Official Feishu Open API. Secrets stay in memory, never in reports."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from .model import METRICS, PLATFORMS, source_link, source_text

API = 'https://open.feishu.cn/open-apis'
TABLE = 'tbljlS0o2H7pigHf'
VIEW = 'vew8YtuXG6'
WIKI = 'GF0OwB4KWiLg5ekIuBxc6dJtnyc'
HISTORY_FIELDS = {'平台': 1, '来源记录ID': 1, '账号': 1, '作品ID': 1, '原始链接': 15,
                  '采集日期': 1, **{v: 2 for v in METRICS.values()}, '采集状态': 1, '采集时间': 5}


class FeishuError(RuntimeError):
    pass


class Feishu:
    def __init__(self, app_id, secret, base_token=''):
        self.token = None
        result = self.request('POST', '/auth/v3/tenant_access_token/internal',
                              {'app_id': app_id.strip(), 'app_secret': secret}, auth=False)
        self.token = result['tenant_access_token']
        self.expires_at = time.monotonic() + result.get('expire', 7200) - 60
        if base_token.strip():
            self.base = base_token.strip()
            if not self.base.isalnum():
                raise FeishuError('多维表格 app_token 格式不正确，请填写 token 而非链接')
        else:
            node = self.request('GET', '/wiki/v2/spaces/get_node', query={'token': WIKI})['data']['node']
            if node.get('obj_type') != 'bitable':
                raise FeishuError('目标知识库节点不是多维表格，请核实链接')
            self.base = node['obj_token']
        self.prefix = '/bitable/v1/apps/' + quote(self.base, safe='')

    def request(self, method, path, body=None, query=None, auth=True):
        if auth and time.monotonic() >= getattr(self, 'expires_at', float('inf')):
            raise FeishuError('飞书应用授权已过期，请重新开始运行')
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        url = API + path + ('?' + urlencode(query) if query else '')
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = Request(url, method=method, headers=headers, data=payload)
        # Read-only queries may retry once; writes never retry blindly.
        for attempt in range(2):
            try:
                with urlopen(req, timeout=30) as response:
                    result = json.load(response)
                break
            except HTTPError as error:
                raise FeishuError(f'飞书接口 HTTP {error.code}，请检查应用权限和网络') from None
            except (URLError, TimeoutError, OSError):
                if attempt == 0 and method == 'GET':
                    time.sleep(1)
                    continue
                raise FeishuError('飞书接口连接失败；若发生在写入阶段，请核对表格后重试') from None
        if result.get('code', 0) != 0:
            # Do not include response bodies: they can contain credentials.
            raise FeishuError(f'飞书接口返回错误码 {result.get("code")}，请检查权限、字段或应用发布状态')
        return result

    def pages(self, path, query=None, body=None):
        query = dict(query or {}, page_size=100)
        records = []
        seen = set()
        while True:
            data = self.request('POST' if body is not None else 'GET', path, body, query)['data']
            records.extend(data.get('items') or [])
            if not data.get('has_more'):
                return records
            token = data.get('page_token')
            if not token or token in seen:
                raise FeishuError('分页结果不完整，停止处理以免漏行')
            seen.add(token)
            query['page_token'] = token

    def fields(self, table):
        return self.pages(self.prefix + f'/tables/{table}/fields')

    def records(self, table, view=None):
        return self.pages(self.prefix + f'/tables/{table}/records/search',
                          body={'automatic_fields': True, **({'view_id': view} if view else {})})

    def get_record(self, table, record):
        return self.request('GET', self.prefix + f'/tables/{table}/records/{record}')['data']['record']

    def validate_fields(self, fields, expected):
        by_name = {}
        for f in fields:
            if f['field_name'] in by_name:
                raise FeishuError('发现重名字段：' + f['field_name'])
            by_name[f['field_name']] = f
        for name, types in expected.items():
            if name not in by_name or by_name[name]['type'] not in types:
                raise FeishuError('字段不存在或类型不匹配：' + name)
        return by_name

    def inspect(self):
        view = self.request('GET', self.prefix + f'/tables/{TABLE}/views/{VIEW}')['data']['view']
        name = view.get('view_name') or view.get('name')
        if name != 'koc置换数据（每日更新）':
            raise FeishuError('视图名称与指定目标不一致：' + str(name))
        tables = self.pages(self.prefix + '/tables')
        source = next((t for t in tables if t['table_id'] == TABLE), None)
        if not source or source['name'] != '内容互动数据表':
            raise FeishuError('目标数据表名称不匹配')
        self.validate_fields(self.fields(TABLE), {'平台': (1, 3), '内容链接': (1, 15),
                             **{v: (2,) for v in METRICS.values()}})
        return self.records(TABLE, VIEW)

    def ensure_fields(self, table, expected):
        existing = {f['field_name']: f for f in self.fields(table)}
        for name, kind in expected.items():
            if name in existing:
                if existing[name]['type'] != kind:
                    raise FeishuError('字段类型不匹配：' + name)
            else:
                self.request('POST', self.prefix + f'/tables/{table}/fields',
                             {'field_name': name, 'type': kind})
        self.validate_fields(self.fields(table), {k: (v,) for k, v in expected.items()})

    def prepare_write(self):
        self.ensure_fields(TABLE, {'采集状态': 1, '采集时间': 5})
        tables = self.pages(self.prefix + '/tables')
        matches = [t for t in tables if t['name'] == '每日数据表']
        if len(matches) > 1:
            raise FeishuError('存在多个“每日数据表”，请先确认保留哪一个')
        if matches:
            history = matches[0]['table_id']
        else:
            history = self.request('POST', self.prefix + '/tables', {'table': {
                'name': '每日数据表', 'default_view_name': '全部记录',
                'fields': [{'field_name': name, 'type': kind} for name, kind in HISTORY_FIELDS.items()]}})['data']['table_id']
        self.ensure_fields(history, HISTORY_FIELDS)
        return history

    def verified_write(self, table, record_id, fields):
        path = self.prefix + f'/tables/{table}/records'
        result = self.request('PUT' if record_id else 'POST', path + ('/' + record_id if record_id else ''), {'fields': fields})
        record_id = record_id or result['data']['record']['record_id']
        saved = self.get_record(table, record_id)['fields']
        for key, value in fields.items():
            actual = saved.get(key)
            if isinstance(value, dict):
                ok = source_link(actual) == value.get('link')
            elif isinstance(value, str):
                ok = source_text(actual) == value
            elif value is None:
                ok = actual is None or actual == [] or actual == ''
            else:
                ok = actual == value
            if not ok:
                raise FeishuError('回读核对失败：' + key + '，请检查记录 ' + record_id)
        return record_id

    def write_source(self, source, observation):
        record_id = source['record_id']
        current = self.get_record(TABLE, record_id)['fields']
        if (source_link(current.get('内容链接')) != source_link(source['fields'].get('内容链接'))
                or source_text(current.get('平台')) != source_text(source['fields'].get('平台'))):
            raise FeishuError('来源链接或平台在采集期间被修改，保留该行原值')
        expected_platform = PLATFORMS.get(source_text(current.get('平台')))
        if expected_platform and expected_platform != observation.platform:
            raise FeishuError('采集结果与来源平台不一致')
        fields = {'采集状态': observation.status, '采集时间': observation.timestamp}
        fields.update({label: observation.values[key] for key, label in METRICS.items()
                       if observation.values.get(key) is not None})
        self.verified_write(TABLE, record_id, fields)

    def write_history(self, table, observation, links, history_records):
        if not observation.daily_key:
            return None
        key = observation.daily_key
        matches = [row for row in history_records if tuple(source_text(row['fields'].get(k))
                   for k in ('平台', '来源记录ID', '采集日期')) == key]
        if len(matches) > 1:
            raise FeishuError('每日数据表已有重复去重键，未覆盖任何历史行')
        fields = dict(zip(('平台', '来源记录ID', '采集日期'), key))
        fields.update({'账号': observation.account or '', '作品ID': observation.work_id or ''})
        fields.update({'原始链接': {'link': links[0], 'text': links[0]},
                       '采集状态': observation.status + ('；同作品原始链接：' + ' | '.join(links) if len(links) > 1 else ''),
                       '采集时间': observation.timestamp})
        fields.update({label: observation.values.get(metric) for metric, label in METRICS.items()})
        record_id = self.verified_write(table, matches[0]['record_id'] if matches else None, fields)
        if matches:
            matches[0]['fields'].update(fields)
        else:
            history_records.append({'record_id': record_id, 'fields': fields})
        return record_id
