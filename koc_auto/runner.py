import json
import os
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from .browser import BrowserCollector, capture_feishu
from .feishu import Feishu, FeishuError, TABLE
from .model import BEIJING, METRICS, PLATFORMS, Observation, platform_for_url, source_link, source_text


def run(app_id, secret, base_token, root, prompt, log, preview, stop):
    root = Path(root)
    lock = root / '.collector.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('已有本地采集任务运行。如果上次异常退出，请先确认没有采集窗口运行，再删除 .collector.lock。') from None
    try:
        with os.fdopen(descriptor, 'w') as file:
            file.write(str(os.getpid()))
        client = Feishu(app_id, secret, base_token)
        secret = None
        sources = client.inspect()
        log(f'已核对表名、视图名和字段类型，目标视图共 {len(sources)} 条记录。')
        if not sources:
            log('目标视图没有记录，未创建字段或历史表。')
            return
        report_dir = root / 'reports' / datetime.now(BEIJING).strftime('%Y%m%d-%H%M%S-%f')
        report_dir.mkdir(parents=True)
        results = []
        with BrowserCollector(root / '.local-browser', report_dir, prompt, log, stop) as browser:
            for index, source in enumerate(sources, 1):
                if stop.is_set():
                    raise RuntimeError('已停止运行；尚未回填。')
                fields = source['fields']
                label = source_text(fields.get('平台'))
                platform = PLATFORMS.get(label)
                url = source_link(fields.get('内容链接'))
                log(f'读取 {index}/{len(sources)}：{source["record_id"]}，{label or "平台为空"}')
                if not platform or platform_for_url(url) != platform:
                    observation = Observation(platform or label, url, outcome='来源字段不匹配',
                                              notes=['平台与链接域名不匹配或链接为空，未打开'])
                else:
                    observation = browser.resolve(url, platform)
                observation = replace(observation, source_record_id=source['record_id'], original_url=url,
                                      values=dict(observation.values), notes=list(observation.notes))
                results.append({'source': source, 'observation': observation, 'write': '未回填', 'history': '未回填'})
                preview(source, observation)
        # Persist only results and source identifiers; no session, cookies, app secret, or tokens.
        def save_report():
            entries = [{'record_id': row['source']['record_id'],
                        'source_platform': source_text(row['source']['fields'].get('平台')),
                        'source_url': source_link(row['source']['fields'].get('内容链接')),
                        'observation': asdict(row['observation']),
                        'write': row['write'], 'history': row['history']} for row in results]
            (report_dir / 'results.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        save_report()
        log('作品读取完毕。请核对窗口中的预览和本地截图。未知指标不会覆盖原值。')
        if stop.is_set() or not prompt('确认回填飞书',
            f'已读取 {len(results)} 条记录。是否回填预览中的已确认指标和采集状态？\n'
            '这也会按需新增“采集状态”“采集时间”和“每日数据表”。\n'
            '请先核对预览；取消则保留本地结果、不写飞书。'):
            log('已取消回填。本地报告：' + str(report_dir))
            return
        history_table = client.prepare_write()
        history_records = client.records(history_table)
        groups = {}
        for row in results:
            observation = row['observation']
            if observation.daily_key:
                groups.setdefault(observation.daily_key, []).append(row)
        # One historical snapshot per platform/account/work/date. Failure in one group does not stop others.
        for rows in groups.values():
            if stop.is_set():
                break
            links = list(dict.fromkeys(source_link(r['source']['fields'].get('内容链接')) for r in rows))
            observation = rows[0]['observation']
            try:
                record = client.write_history(history_table, observation, links, history_records)
                for row in rows:
                    row['history'] = '已保存并回读核对：' + record
            except FeishuError as error:
                observation.notes.append('每日历史保存失败：' + str(error))
                for row in rows:
                    row['history'] = '失败：' + str(error)
            save_report()
        for row in results:
            if stop.is_set():
                row['write'] = '用户停止，未回填'
                continue
            observation = row['observation']
            if not observation.daily_key:
                row['history'] = '未生成：飞书来源记录ID未确认'
            try:
                client.write_source(row['source'], observation)
                row['write'] = '已保存并回读核对'
                log('已回读核对来源记录：' + row['source']['record_id'])
            except FeishuError as error:
                row['write'] = '失败：' + str(error)
                log(row['source']['record_id'] + '：' + str(error))
            save_report()
        summary = {'总行数': len(results), '成功行数': 0, '部分成功行数': 0, '失败行数': 0,
                   '重复作品链接': [], '需核实或替换链接': [], '失败记录': []}
        for row in results:
            observation = row['observation']
            confirmed = sum(v is not None for v in observation.values.values())
            written = row['write'] == '已保存并回读核对'
            history_ok = row['history'].startswith('已保存并回读核对')
            if written and confirmed == 4 and history_ok:
                summary['成功行数'] += 1
            elif written and confirmed:
                summary['部分成功行数'] += 1
            else:
                summary['失败行数'] += 1
                summary['失败记录'].append({'记录ID': row['source']['record_id'],
                    '链接': source_link(row['source']['fields'].get('内容链接')),
                    '原因': observation.status, '回填': row['write'], '历史': row['history']})
            if observation.outcome == '非作品链接':
                summary['需核实或替换链接'].append(source_link(row['source']['fields'].get('内容链接')))
        duplicates = {}
        for row in results:
            observation = row['observation']
            if observation.work_id:
                duplicates.setdefault((observation.platform, observation.work_id), []).append(row)
        for rows in duplicates.values():
            if len(rows) > 1:
                summary['重复作品链接'].append({'作品ID': rows[0]['observation'].work_id,
                    '链接': [source_link(r['source']['fields'].get('内容链接')) for r in rows],
                    '记录ID': [r['source']['record_id'] for r in rows]})
        (report_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        log('完成：' + '，'.join(f'{k} {summary[k]}' for k in ('总行数', '成功行数', '部分成功行数', '失败行数')))
        log('完整报告及作品截图：' + str(report_dir))
        summary['飞书结果截图'] = capture_feishu(root / '.local-browser', report_dir, prompt, log, stop)
        (report_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        log('每日自动运行未启用。截图需人工登录和调整页面；跳过截图不等于已完成截图。')
    finally:
        lock.unlink(missing_ok=True)
