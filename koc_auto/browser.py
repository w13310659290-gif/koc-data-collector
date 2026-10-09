"""Read visible work controls only. Fail closed when layout is ambiguous."""
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .model import BEIJING, Observation, METRICS, count_value, platform_for_url, profile_account, work_id

FEISHU_URL = 'https://dq5fw5z865q.feishu.cn/wiki/GF0OwB4KWiLg5ekIuBxc6dJtnyc?table=tbljlS0o2H7pigHf&view=vew8YtuXG6'

EXTRACT = r'''platform => {
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
  const all = (root, selector) => [...root.querySelectorAll(selector)].filter(visible);
  const chooseRoot = selectors => {
    for (const selector of selectors) {
      const candidates = all(document, selector);
      if (candidates.length === 1) return candidates[0];
    }
    return null;
  };
  const root = platform === 'xiaohongshu'
    ? chooseRoot(['.note-detail-mask .note-container', '.note-detail-mask', '#noteContainer', '.note-container'])
    : chooseRoot(['[data-e2e="video-detail"]', '[data-e2e="video-detail-container"]']);
  const scope = root || document;
  // Comment rows also have like-wrapper; only the work bar has all three controls.
  const workBars = root ? all(root, '.engage-bar, .interaction-container').filter(el =>
    el.querySelector('.like-wrapper') && el.querySelector('.collect-wrapper') && el.querySelector('.chat-wrapper')) : [];
  const barLeaves = workBars.filter(el => !workBars.some(other => other !== el && el.contains(other)));
  const metricScope = platform === 'xiaohongshu' ? (barLeaves.length === 1 ? barLeaves[0] : null) : scope;
  const selectors = platform === 'xiaohongshu' ? {
    likes: ['.like-wrapper .count'],
    favorites: ['.collect-wrapper .count'],
    comments: ['.chat-wrapper .count'],
    shares: ['.share-wrapper .count']
  } : {
    likes: ['[data-e2e="video-player-digg"]', '[data-e2e="video-player-like"]'],
    comments: ['[data-e2e="video-player-comment"]', '[data-e2e="video-player-comments"]'],
    favorites: ['[data-e2e="video-player-collect"]', '[data-e2e="video-player-favorite"]'],
    shares: ['[data-e2e="video-player-share"]']
  };
  const raw = {}, notes = [], evidence = {};
  for (const [key, candidates] of Object.entries(selectors)) {
    const found = new Set();
    const elements = new Set();
    for (const selector of candidates) {
      // XHS requires the open note modal; do not read feed cards.
      if (!metricScope) continue;
      for (const el of all(metricScope, selector)) {
        const text = (el.innerText || '').trim();
        if (text && text.length <= 40) {
          elements.add(el);
          found.add(text);
          evidence[key] = selector;
        }
      }
    }
    const leaves = [...elements].filter(el => ![...elements].some(other => other !== el && el.contains(other)));
    if (found.size === 1 && leaves.length === 1) raw[key] = [...found][0];
    else if (leaves.length > 1) notes.push(key + '存在多个可见控件，未采集');
  }
  const authorSelectors = platform === 'xiaohongshu'
    ? ['.author-container a[href*="/user/profile/"]', '.author a[href*="/user/profile/"]',
       'a.author[href*="/user/profile/"]', 'a.author-container[href*="/user/profile/"]', '.author-wrapper a[href*="/user/profile/"]']
    : ['[data-e2e="video-author-name"] a[href*="/user/"]', 'a[data-e2e="video-author-name"][href*="/user/"]',
       '[data-e2e="video-info"] a[href*="/user/"]', '[data-e2e="video-author"] a[href*="/user/"]'];
  const authors = new Set();
  for (const selector of authorSelectors) for (const el of all(scope, selector)) {
    const url = new URL(el.href);
    authors.add(url.origin + url.pathname);
  }
  const body = document.body.innerText || '';
  const automated = /访问过于频繁|操作过于频繁|异常访问|自动化访问|机器人访问|访问频率异常/.test(body);
  const verification = /请完成验证|请通过验证|拖动滑块|安全验证|验证码验证/.test(body);
  const unavailable = /作品已删除|笔记已删除|内容已删除|笔记不存在|作品不存在|该内容无法展示|该笔记暂时无法浏览/.test(body);
  const login = /登录后查看|登录后可查看|请登录后|登录后继续|扫码登录/.test(body);
  return {raw, notes, evidence, author: authors.size === 1 ? [...authors][0] : null,
          automated, verification, unavailable, login,
          rootFound: !!root, hasMetrics: Object.keys(raw).length > 0};
}'''

# Narrow diagnostics: component attributes and numeric text, never cookies, inputs or full HTML.
DIAGNOSTICS = r'''() => {
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const describe = el => ({tag: el.tagName, class: String(el.className?.baseVal ?? el.className ?? '').slice(0,250),
    e2e: el.getAttribute('data-e2e'), role: el.getAttribute('role')});
  const controls = [];
  for (const el of document.querySelectorAll('[data-e2e], .interaction-container .count, .engage-bar .count, .author-container a, a.author, .author a')) {
    if (!visible(el)) continue;
    const marker = el.getAttribute('data-e2e') || '';
    if (marker && !/video|like|digg|comment|collect|share|author/.test(marker)) continue;
    const text = (el.innerText || '').trim();
    const item = {node: describe(el), parents: [], numericText: /^[\d\s.,万亿wWkK]+$/.test(text) && text.length < 30 ? text : null};
    let parent = el.parentElement;
    for (let i=0; parent && i<3; i++, parent=parent.parentElement) item.parents.push(describe(parent));
    if (el.tagName === 'A' && /\/user(?:\/profile)?\//.test(el.getAttribute('href') || '')) {
      const url = new URL(el.href); item.publicAuthorPath = url.origin + url.pathname;
    }
    controls.push(item);
    if (controls.length >= 200) break;
  }
  return {controls, truncated: controls.length >= 200};
}'''


class BrowserCollector:
    def __init__(self, profile, report_dir, prompt, log, stop):
        self.profile = Path(profile)
        self.report_dir = Path(report_dir)
        self.prompt = prompt
        self.log = log
        self.stop = stop
        self.blocked = set()
        self.cache = {}
        self.aliases = {}
        self.login_prepared = set()
        self.login_skipped = set()

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.engine = sync_playwright().start()
        try:
            self.context = self.engine.chromium.launch_persistent_context(
                str(self.profile), channel='msedge', headless=False,
                viewport={'width': 1440, 'height': 960}, accept_downloads=False)
        except Exception:
            self.engine.stop()
            raise RuntimeError('无法启动 Edge。请确认已安装 Edge，且另一次采集工具没有使用同一浏览器资料目录。') from None
        return self

    def __exit__(self, *args):
        self.context.close()
        self.engine.stop()

    def prepare_xhs_login(self):
        """Human login in the same browser context used for collection."""
        platform = 'xiaohongshu'
        if platform in self.login_prepared:
            return True
        if platform in self.login_skipped or self.stop.is_set():
            return False
        page = self.context.new_page()
        try:
            page.goto('https://www.xiaohongshu.com/explore', wait_until='domcontentloaded', timeout=30000)
            page.bring_to_front()
            confirmed = self.prompt('先登录小红书',
                '已打开本工具专用的 Edge 小红书页面。\n'
                '请在该页面点击“登录”，用手机扫码或网站安全表单完成登录。\n'
                '如果已经登录，确认页面显示的是你的账号。\n'
                '完成后回到此提示框，点击“确定”才开始读取作品；取消则跳过本次小红书采集。\n'
                '不要在其他普通 Edge 窗口登录，不要发送密码、验证码或 Cookie。')
            if confirmed and not self.stop.is_set():
                self.login_prepared.add(platform)
                return True
            self.login_skipped.add(platform)
            return False
        except Exception:
            self.log('小红书登录页面打开失败，本次保留原值。')
            self.login_skipped.add(platform)
            return False
        finally:
            page.close()

    def resolve(self, url, platform):
        if self.stop.is_set():
            raise RuntimeError('用户已停止运行')
        if platform == 'xiaohongshu' and not self.prepare_xhs_login():
            return Observation(platform, url, outcome='需要登录', notes=['小红书预登录未完成，本次未读取作品'])
        known = work_id(url, platform)
        if platform in self.blocked:
            return Observation(platform, url, outcome='访问受限', notes=['本网站已出现自动化限制，本次不再访问'])
        if known and (platform, known) in self.cache:
            self.aliases[url] = (platform, known)
            return self.cache[platform, known]
        if url in self.aliases:
            return self.cache[self.aliases[url]]
        page = self.context.new_page()
        observation = Observation(platform, url)
        try:
            for attempt in range(2):
                try:
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(2000)
                    break
                except Exception:
                    if attempt:
                        observation.notes.append('普通加载失败，已重试一次')
                        return observation
            identity = work_id(page.url, platform)
            if platform_for_url(page.url) != platform or not identity:
                # Login redirects can lose the work URL; allow a human login before classifying.
                if re.search(r'login|passport', page.url, re.I):
                    if not self.prompt('需要登录', '请在 Edge 中完成登录，再点击“已完成，继续”。不要把凭证发到聊天。'):
                        observation.outcome = '需要登录'
                        return observation
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(2000)
                    identity = work_id(page.url, platform)
                if not identity or platform_for_url(page.url) != platform:
                    nonwork = re.search(r'/(?:user|shop|product|goods)(?:/|$)', urlsplit(page.url).path)
                    visible_work = page.evaluate(EXTRACT, platform)
                    if (nonwork or platform_for_url(page.url) != platform or
                            not (visible_work['rootFound'] and visible_work['hasMetrics'])):
                        observation.outcome = '非作品链接' if nonwork else '作品页面未确认'
                        observation.notes.append('未确认当前作品页面结构，保留原值，不采集主页汇总数据')
                        return observation
            if known and known != identity:
                observation.notes.append('跳转后的作品ID与原链接不一致，未采集')
                return observation
            observation.work_id = identity
            cache_key = platform, identity or url
            if cache_key in self.cache:
                self.aliases[url] = cache_key
                return self.cache[cache_key]
            data = page.evaluate(EXTRACT, platform)
            for _ in range(2):
                if data['automated']:
                    self.blocked.add(platform)
                    observation.outcome = '访问受限'
                    observation.notes.append('网站明确提示访问频率或自动化限制，停止该网站采集')
                    return observation
                if data['unavailable']:
                    return observation
                if data['verification'] or (data['login'] and not data['hasMetrics']):
                    title = '需要人工验证' if data['verification'] else '需要登录'
                    if not self.prompt(title, '请只在 Edge 中完成登录或人工验证，再点击“已完成，继续”。也可跳过此作品。'):
                        observation.outcome = title
                        return observation
                    if work_id(page.url, platform) != identity:
                        page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(1500)
                    data = page.evaluate(EXTRACT, platform)
                    continue
                break
            if data['automated']:
                self.blocked.add(platform)
                observation.outcome = '访问受限'
                return observation
            if data['verification'] or (data['login'] and not data['hasMetrics']):
                observation.outcome = '需要人工验证' if data['verification'] else '需要登录'
                return observation
            # Dynamic controls may appear after the initial document load.
            expected = ('likes', 'comments', 'favorites') if platform == 'xiaohongshu' else tuple(METRICS)
            for _ in range(8):
                if self.stop.is_set() or data['automated'] or data['verification'] or data['unavailable']:
                    break
                if all(count_value(data['raw'].get(key))[0] is not None for key in expected) and data['author']:
                    break
                page.wait_for_timeout(1000)
                data = page.evaluate(EXTRACT, platform)
            if self.stop.is_set():
                observation.notes.append('用户已停止读取，未使用未核对数据')
                return observation
            if data['automated']:
                self.blocked.add(platform)
                observation.outcome = '访问受限'
                return observation
            if data['verification'] or data['unavailable']:
                observation.outcome = '需要人工验证' if data['verification'] else '作品不可访问'
                return observation
            if (identity and work_id(page.url, platform) != identity) or platform_for_url(page.url) != platform:
                observation.notes.append('读取前作品ID已变化，未采集')
                return observation
            observation.account = profile_account(data['author'] or '', platform)
            observation.captured_at = datetime.now(BEIJING).isoformat()
            observation.notes.extend(data['notes'])
            observation.raw = data['raw']
            for metric in METRICS:
                value, approximate = count_value(data['raw'].get(metric))
                observation.values[metric] = value
                if approximate:
                    observation.notes.append(METRICS[metric] + '：' + approximate)
            confirmed = sum(v is not None for v in observation.values.values())
            observation.outcome = '成功' if confirmed == 4 else '部分成功' if confirmed else '页面结构未识别'
            if not confirmed:
                observation.notes.append('未找到唯一且可见的作品互动控件，保留所有原值')
            self.report_dir.mkdir(parents=True, exist_ok=True)
            file_key = identity or hashlib.sha256(url.encode()).hexdigest()[:20]
            (self.report_dir / (platform + '-' + file_key + '-controls.json')).write_text(
                json.dumps(page.evaluate(DIAGNOSTICS), ensure_ascii=False, indent=2), encoding='utf-8')
            screenshot = self.report_dir / (platform + '-' + file_key + '.png')
            page.screenshot(path=str(screenshot), full_page=False)
            observation.screenshot = str(screenshot)
            self.cache[cache_key] = observation
            self.aliases[url] = cache_key
            return observation
        except Exception:
            observation.outcome = '作品不可访问'
            observation.notes.append('浏览器读取失败，原值保留；可查看本地浏览器页面诊断')
            return observation
        finally:
            page.close()


def capture_feishu(profile, report_dir, prompt, log, stop):
    """Capture the real Feishu page after human login/column positioning, no UI writes."""
    if stop.is_set() or not prompt('保存飞书结果截图',
            '回填核对已结束。是否打开目标飞书视图保存真实页面截图？\n'
            '如果需要登录，请在 Edge 中扫码；随后将四项指标及采集状态滚动到可见位置。'):
        return []
    screenshots = []
    try:
        with BrowserCollector(profile, report_dir, prompt, log, stop) as browser:
            page = browser.context.new_page()
            page.goto(FEISHU_URL, wait_until='domcontentloaded', timeout=30000)
            for index in range(1, 6):
                if stop.is_set() or not prompt('调整飞书页面后截图',
                        '请确认 Edge 已显示指定表格和视图，并将要截图的记录、指标列调整到可见位置。\n'
                        '准备好后点“确定”保存一张真实网页截图；点“取消”结束截图。\n'
                        '最多保存5张，可用不同横向/纵向位置覆盖全部记录。'):
                    break
                current = urlsplit(page.url)
                if current.hostname != 'dq5fw5z865q.feishu.cn' or not current.path.startswith('/wiki/GF0OwB4KWiLg5ekIuBxc6dJtnyc'):
                    log('当前不是指定飞书表格页面，未保存结果截图。')
                    break
                screenshot = Path(report_dir) / f'feishu-result-{index}.png'
                page.screenshot(path=str(screenshot), full_page=False)
                screenshots.append(str(screenshot))
                log('已保存飞书真实页面截图：' + str(screenshot))
    except Exception:
        log('飞书截图未完成。已核对的接口写入结果保持有效；请在自己的飞书网页中检查和截图。')
    return screenshots
