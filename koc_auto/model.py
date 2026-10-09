import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

BEIJING = timezone(timedelta(hours=8))
METRICS = {'likes': '点赞数', 'comments': '评论数', 'favorites': '收藏数', 'shares': '转发数'}
PLATFORMS = {'抖音': 'douyin', '小红书': 'xiaohongshu'}


def count_value(raw):
    """Unknown is None, including icons without counts. Never invent a zero."""
    raw = str(raw or '').strip()
    compact = raw.replace(',', '').replace('，', '')
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(万|亿|[wWkK])?', compact)
    if not match:
        return None, None
    number, suffix = match.groups()
    if not suffix and '.' in number:
        return None, None
    multipliers = {'万': 10000, '亿': 100000000, 'w': 10000, 'k': 1000}
    value = Decimal(number) * (multipliers[suffix.lower()] if suffix else 1)
    if value != value.to_integral_value() or value > 9007199254740991:
        return None, None
    return int(value), ('页面缩写，约' + raw if suffix else None)


def source_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(source_text(part) for part in value)
    if isinstance(value, dict):
        return str(value.get('text', ''))
    return ''


def source_link(value):
    if isinstance(value, dict):
        candidate = value.get('link') or value.get('url') or value.get('text') or ''
        return candidate.strip() if isinstance(candidate, str) else ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and len(value) == 1:
        return source_link(value[0])
    return ''


def platform_for_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or '').lower()
        if parsed.port not in (None, 80, 443):
            return None
        for platform, domains in [('douyin', ('douyin.com', 'iesdouyin.com')),
                                  ('xiaohongshu', ('xiaohongshu.com', 'xhslink.com', 'xhslink.cn'))]:
            if any(host == d or host.endswith('.' + d) for d in domains):
                return platform
    except ValueError:
        pass
    return None


def work_id(url, platform):
    if platform_for_url(url) != platform:
        return None
    path = urlsplit(url).path
    pattern = r'/(?:video|note)/(\d+)(?:/|$)' if platform == 'douyin' else r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})(?:/|$)'
    match = re.search(pattern, path)
    if match:
        return match.group(1).lower()
    if platform == 'douyin' and path.rstrip('/') in ('', '/jingxuan', '/discover'):
        values = parse_qs(urlsplit(url).query).get('modal_id', [])
        if len(values) == 1 and re.fullmatch(r'\d+', values[0]):
            return values[0]
    return None


def profile_account(url, platform):
    if platform_for_url(url) != platform:
        return None
    pattern = r'/user/([^/?#]+)' if platform == 'douyin' else r'/user/profile/([a-fA-F0-9]{24})'
    match = re.search(pattern, urlsplit(url).path)
    return match.group(1) if match else None


@dataclass
class Observation:
    platform: str
    original_url: str
    work_id: str | None = None
    account: str | None = None
    values: dict = field(default_factory=lambda: {key: None for key in METRICS})
    raw: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    outcome: str = '作品不可访问'
    screenshot: str | None = None
    captured_at: str = field(default_factory=lambda: datetime.now(BEIJING).isoformat())
    source_record_id: str | None = None

    @property
    def status(self):
        missing = [label for key, label in METRICS.items() if self.values.get(key) is None]
        details = list(self.notes)
        if missing and self.outcome in ('成功', '部分成功'):
            details.append('未显示或未确认：' + '、'.join(missing) + '；原值保留')
        return '；'.join([self.outcome, *dict.fromkeys(details)])

    @property
    def date(self):
        return datetime.fromisoformat(self.captured_at).astimezone(BEIJING).date().isoformat()

    @property
    def timestamp(self):
        return int(datetime.fromisoformat(self.captured_at).timestamp() * 1000)

    @property
    def daily_key(self):
        if not self.source_record_id:
            return None
        return self.platform, self.source_record_id, self.date
