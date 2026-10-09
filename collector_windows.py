"""Run this GUI locally on Windows. No credentials are written to disk."""
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from koc_auto.model import METRICS
from koc_auto.runner import run

ROOT = Path(__file__).resolve().parent


class App:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title('KOC 采集与飞书回填 · Windows')
        self.window.geometry('1080x780')
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.pending = None
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='本地 Edge 读取作品 · 飞书官方接口回填', font=('', 17, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='首次运行需为飞书自建应用授权目标表格。请在下方本地填写凭证，不要发到聊天。').pack(anchor='w', pady=(8, 12))
        self.entries = {}
        for key, label, hidden in [('app_id', '飞书 App ID', False), ('secret', '飞书 App Secret（仅本次运行使用）', True),
                                   ('base_token', '多维表格 app_token（可留空，默认从指定知识库链接解析）', False)]:
            row = ttk.Frame(frame)
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=label, width=60).pack(side='left')
            entry = ttk.Entry(row, show='*' if hidden else '')
            entry.pack(side='left', fill='x', expand=True)
            self.entries[key] = entry
        ttk.Label(frame, text='固定目标：内容互动数据表 / koc置换数据（每日更新）\n未知数据保留原值；页面缩写标注约数；登录和验证码只在 Edge 内完成。').pack(anchor='w', pady=10)
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=8)
        self.start_button = ttk.Button(actions, text='1. 读取全部作品并预览', command=self.start)
        self.start_button.pack(side='left')
        ttk.Button(actions, text='停止后续操作', command=self.cancel).pack(side='left', padx=8)
        ttk.Button(actions, text='打开本地报告文件夹', command=self.open_reports).pack(side='left')
        columns = ('id', 'platform', *METRICS, 'status')
        self.table = ttk.Treeview(frame, columns=columns, show='headings', height=10)
        for key in columns:
            self.table.heading(key, text={'id': '来源记录ID', 'platform': '平台', 'status': '采集状态', **METRICS}[key])
            self.table.column(key, width=140 if key == 'id' else 240 if key == 'status' else 80, stretch=key == 'status')
        self.table.pack(fill='both', expand=True)
        ttk.Label(frame, text='预览“—”表示未确认，不会写成 0。读取完成后会询问是否回填。').pack(anchor='w', pady=6)
        self.logs = tk.Text(frame, height=11, wrap='word', state='disabled')
        self.logs.pack(fill='both', expand=True)
        self.window.after(100, self.poll)

    def log(self, text):
        self.events.put(('log', text))

    def ask(self, title, text):
        signal = threading.Event()
        reply = []
        self.events.put(('prompt', (title, text, signal, reply)))
        while not signal.wait(.2):
            if self.stop.is_set():
                return False
        return bool(reply and reply[0]) and not self.stop.is_set()

    def preview(self, source, observation):
        self.events.put(('preview', (source['record_id'], observation.platform,
                                    *[observation.values.get(k) if observation.values.get(k) is not None else '—' for k in METRICS], observation.status)))

    def calibrate(self, counts):
        signal = threading.Event()
        reply = []
        self.events.put(('calibrate', (counts, signal, reply)))
        while not signal.wait(.2):
            if self.stop.is_set():
                return None
        return reply[0] if reply and not self.stop.is_set() else None

    def start(self):
        if self.running:
            return
        values = {k: e.get().strip() for k, e in self.entries.items()}
        if not values['app_id'] or not values['secret']:
            messagebox.showinfo('需要飞书应用配置', '请先在飞书开放平台创建并发布自建应用，授权表格后，在此填写 App ID 和 App Secret。')
            return
        self.running = True
        self.stop.clear()
        self.start_button.state(['disabled'])
        for entry in self.entries.values():
            entry.state(['disabled'])
        self.table.delete(*self.table.get_children())
        def worker():
            try:
                run(**values, root=ROOT, prompt=self.ask, log=self.log, preview=self.preview, stop=self.stop, calibrate=self.calibrate)
            except Exception as error:
                from koc_auto.feishu import FeishuError
                self.log(str(error) if isinstance(error, (FeishuError, RuntimeError)) else '运行异常，请检查网络和本地环境；未自动重试写入。')
            finally:
                values['secret'] = ''
                self.events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.logs.configure(state='normal')
                self.logs.insert('end', data + '\n')
                self.logs.see('end')
                self.logs.configure(state='disabled')
            elif kind == 'preview':
                self.table.insert('', 'end', values=data)
            elif kind == 'prompt':
                title, text, signal, reply = data
                if self.stop.is_set():
                    reply.append(False)
                else:
                    reply.append(messagebox.askokcancel(title, text, parent=self.window))
                signal.set()
            elif kind == 'done':
                self.running = False
                self.start_button.state(['!disabled'])
                for entry in self.entries.values():
                    entry.state(['!disabled'])
                self.entries['secret'].delete(0, 'end')
            elif kind == 'calibrate':
                counts, signal, reply = data
                result = None
                while not self.stop.is_set():
                    answer = simpledialog.askstring('确认抖音作品图标（相同图形只需一次）',
                        '请看 Edge 中红框编号对应的实际图标，不要点击作品按钮。\n'
                        '按“点赞、评论、收藏、转发”的顺序填写4个编号，用逗号分隔。\n'
                        '不知道某项填0；点取消则跳过标注。不要只按排列顺序猜。\n'
                        '对应数字：' + '，'.join(f'{i+1}号={count}' for i,count in enumerate(counts)), parent=self.window)
                    if answer is None:
                        break
                    try:
                        indices = [int(part.strip()) for part in answer.replace('，',',').split(',')]
                        nonzero = [i for i in indices if i]
                        if len(indices)!=4 or any(i<0 or i>len(counts) for i in indices) or len(set(nonzero))!=len(nonzero):
                            raise ValueError()
                        result = dict(zip(METRICS,indices))
                        break
                    except ValueError:
                        messagebox.showinfo('编号格式不正确','请填写4个编号；不同指标不能使用同一个编号。不知道填0。',parent=self.window)
                reply.append(result)
                signal.set()
        self.window.after(100, self.poll)

    def cancel(self):
        self.stop.set()
        self.log('已请求停止；正在进行的单次请求可能已保存，请等待核对结果。')

    def close(self):
        if self.running:
            self.cancel()
            messagebox.showinfo('正在安全停止', '请等当前请求完成后再关闭窗口，避免无法核对写入结果。')
        else:
            self.window.destroy()

    def open_reports(self):
        path = ROOT / 'reports'
        path.mkdir(exist_ok=True)
        if os.name == 'nt':
            os.startfile(path)
        else:
            messagebox.showinfo('报告位置', str(path))


if __name__ == '__main__':
    App().window.mainloop()
