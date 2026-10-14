"""fire desktop application. All Playwright work stays off Tk's UI thread."""
import json
import base64
import math
import os
import queue
import threading
import time
import uuid
from io import BytesIO
from contextlib import contextmanager
import tempfile
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from zoneinfo import ZoneInfo

import fire

ROOT = Path(__file__).resolve().parent
SETTINGS = fire.DATA / 'desktop.json'
PROFILE = fire.AUTH / 'profile.json'
JOURNAL = fire.AUTH / 'schedule.json'
WORKER_STATE = threading.local()


def close_cached_browser():
    record = getattr(WORKER_STATE, 'browser', None)
    WORKER_STATE.browser = None
    if record:
        try:
            record[1].close()
        finally:
            record[0].stop()


@contextmanager
def browser_scope(headless=True, reuse=True):
    from playwright.sync_api import sync_playwright
    cached_mode = getattr(WORKER_STATE, 'cache_enabled', False)
    if cached_mode and headless and reuse:
        record = getattr(WORKER_STATE, 'browser', None)
        if record and record[1].is_connected() and not record[3].is_closed():
            yield record[1], record[2], record[3], False
            return
        close_cached_browser()
        engine = sync_playwright().start()
        try:
            browser = launch_browser(engine, headless=True)
            context = browser.new_context(storage_state=str(fire.STATE))
            page = context.new_page()
        except Exception:
            engine.stop()
            raise
        WORKER_STATE.browser = (engine, browser, context, page)
        try:
            yield browser, context, page, True
        except Exception:
            close_cached_browser()
            raise
    else:
        if cached_mode:
            close_cached_browser()
        with sync_playwright() as engine:
            browser = launch_browser(engine, headless=headless)
            try:
                context = browser.new_context(storage_state=str(fire.STATE) if reuse and fire.STATE.exists() else None)
                yield browser, context, context.new_page(), True
            finally:
                browser.close()


def launch_browser(playwright, headless=False):
    bundled = ROOT / 'browser' / 'chrome-win64' / 'chrome.exe'
    if bundled.exists():
        return playwright.chromium.launch(executable_path=str(bundled), headless=headless)
    edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if edge.exists():
        return playwright.chromium.launch(channel='msedge', headless=headless)
    return playwright.chromium.launch(headless=headless)


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def validate(data):
    names = data['friends']
    if not names or len(names) > 20 or len(set(names)) != len(names):
        raise ValueError('请选择 1–20 位好友，名称不可重复')
    if not data['text'].strip() and not data['image']:
        raise ValueError('请填写文字或选择表情包图片')
    seconds = float(data['interval'])
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('发送间隔应为非负数字')
    if data['image'] and not Path(data['image']).is_file():
        raise ValueError('表情包图片不存在，请重新选择')
    if data['image'] and Path(data['image']).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        raise ValueError('请选择 PNG、JPG、GIF 或 WebP 图片')
    parsed = datetime.strptime(data['time'], '%H:%M')
    data['time'] = parsed.strftime('%H:%M')
    data['interval'] = seconds
    return data


def schedule_key(now, clock):
    # Exact minute only: never send late just because the app was opened.
    return now.strftime('%Y-%m-%d') + ' ' + clock if now.strftime('%H:%M') == clock else None


def selectors():
    path = fire.DATA / 'config.json'
    defaults = read_json(ROOT / 'config.example.json', {})['selectors']
    return {**defaults, **read_json(path, {}).get('selectors', {})}


def visible(page, selector):
    return any(node.is_visible() for node in page.locator(selector).all())


def self_nickname(payload):
    """Accept only the self-profile response, never conversation participants."""
    if not isinstance(payload, dict) or payload.get('status_code', 0) != 0:
        return ''
    user = payload.get('user', {})
    name = user.get('nickname') if isinstance(user, dict) else None
    return name.strip() if isinstance(name, str) else ''


def capture_self_response(response, result):
    if '/user/profile/self/' in response.url and response.ok:
        try:
            payload = response.json()
            name = self_nickname(payload)
            if name:
                result['name'] = name
            if isinstance(payload, dict) and payload.get('status_code', 0) == 0:
                user = payload.get('user', {})
                for key in ('avatar_thumb', 'avatar_medium', 'avatar_larger'):
                    urls = user.get(key, {}).get('url_list', []) if isinstance(user, dict) else []
                    if urls and isinstance(urls[0], str) and urls[0].startswith('https://'):
                        result['avatar_url'] = urls[0]
                        break
        except Exception:
            pass


def read_account_name(context, result):
    if result.get('name') and result.get('avatar_url'):
        return result['name']
    # The chat page often omits the current user's nickname entirely.
    # Open the user's own profile in a separate page to obtain it.
    profile = context.new_page()
    profile.on('response', lambda response: capture_self_response(response, result))
    try:
        profile.goto('https://www.douyin.com/user/self', wait_until='domcontentloaded', timeout=20000)
        for _ in range(16):
            if result.get('name') and result.get('avatar_url'):
                return result['name']
            for node in profile.locator('[data-e2e="user-avatar"] img, img[data-e2e="user-avatar"], .avatar-component-avatar-container img').all():
                src = node.evaluate('(node) => node.currentSrc || node.src')
                if node.is_visible() and isinstance(src, str) and src.startswith('https://'):
                    result['avatar_url'] = src
                    break
            for selector in ('[data-e2e="user-title"]', '[data-e2e="user-info"] [class*="nickname"]'):
                for node in profile.locator(selector).all():
                    if node.is_visible():
                        name = node.inner_text().strip()
                        if name and name not in ('我', '我的', '个人主页'):
                            result['name'] = name
            profile.wait_for_timeout(500)
    except Exception:
        pass
    finally:
        profile.close()
    return result.get('name', '')


def cache_avatar(context, account, reuse):
    previous = read_json(PROFILE, {}).get('avatar', '') if reuse else ''
    if previous and Path(previous).is_file():
        return previous
    url = account.get('avatar_url')
    if not url:
        return previous
    try:
        from PIL import Image, ImageDraw, ImageOps
        response = context.request.get(url, timeout=10000)
        if not response.ok:
            return previous
        body = response.body()
        if len(body) > 5 * 1024 * 1024:
            return previous
        with Image.open(BytesIO(body)) as source:
            image = ImageOps.fit(source.convert('RGBA'), (56, 56), method=Image.Resampling.LANCZOS)
        mask = Image.new('L', (56, 56), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 55, 55), fill=255)
        image.putalpha(mask)
        target = fire.AUTH / 'avatar.png'
        image.save(target)
        return str(target)
    except Exception:
        return previous


def browser_login(reuse, emit):
    from playwright.sync_api import sync_playwright
    if reuse and not fire.STATE.exists():
        raise RuntimeError('没有已保存的登录状态，请点击“扫码登录 / 切换账号”')
    if reuse:
        emit('progress', (1, 10))
    with browser_scope(headless=reuse, reuse=reuse) as (browser, context, page, fresh):
        if reuse:
            emit('progress', (2, 10))
        if reuse:
            emit('progress', (3, 10))
        account = {}
        if fresh:
            page.on('response', lambda response: capture_self_response(response, account))
        if fresh:
            page.goto(fire.URL, wait_until='commit')
        page.set_default_timeout(3000)
        if reuse:
            emit('progress', (4, 10))
            emit('status', '正在后台验证已保存账号，无需打开浏览器…')
            try:
                fire.wait_chat_ready(page, selectors()['search'])
            except RuntimeError as exc:
                raise RuntimeError('已保存账号无法通过后台验证，请检查网络；若登录已过期，请点击“扫码登录 / 切换账号”重新扫码。') from exc
            cookies = context.cookies('https://www.douyin.com')
            emit('progress', (7, 10))
            if not any(c['name'] in ('sessionid', 'sessionid_ss') and c['value'] for c in cookies):
                raise RuntimeError('登录状态已失效，请点击“扫码登录 / 切换账号”重新扫码')
            name = account.get('name') or read_json(PROFILE, {}).get('name', '')
            emit('progress', (8, 10))
            if not name:
                name = read_account_name(context, account)
            if not account.get('avatar_url') and not read_json(PROFILE, {}).get('avatar'):
                read_account_name(context, account)
            avatar = cache_avatar(context, account, reuse=True)
            emit('progress', (9, 10))
            context.storage_state(path=str(fire.STATE), indexed_db=True)
            write_json(fire.AUTH / 'cookies.json', cookies)
            emit('progress', (10, 10))
            emit('logged_in', {'name': name, 'reuse': True, 'avatar': avatar})
            return
        emit('status', '请在弹出的抖音浏览器中扫码；若有短信验证请一起完成')
        # Use the real website QR flow, never collect a password in this app.
        for caption in ('登录', '扫码登录'):
            try:
                buttons = page.get_by_text(caption, exact=True)
                candidates = [n for n in buttons.all() if n.is_visible()]
                if len(candidates) == 1:
                    candidates[0].click(timeout=1500)
            except Exception:
                pass
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if page.is_closed():
                raise RuntimeError('登录浏览器已关闭，请重新点击登录')
            cookies = context.cookies('https://www.douyin.com')
            session = any(c['name'] in ('sessionid', 'sessionid_ss') and c['value'] for c in cookies)
            if session and visible(page, selectors()['search']) and visible(page, selectors()['result']):
                break
            page.wait_for_timeout(1000)
        else:
            raise RuntimeError('登录等待超时，或无法识别私信页面；请重新扫码')
        emit('status', '登录完成，正在读取本人主页昵称…')
        name = read_account_name(context, account)
        avatar = cache_avatar(context, account, reuse=False)
        context.storage_state(path=str(fire.STATE), indexed_db=True)
        write_json(fire.AUTH / 'cookies.json', cookies)
        emit('logged_in', {'name': name, 'reuse': reuse, 'avatar': avatar})


def send_round(data, dry_run, emit):
    from playwright.sync_api import sync_playwright
    with browser_scope(headless=True, reuse=True) as (browser, context, page, fresh):
        if fresh:
            page.goto(fire.URL, wait_until='commit')
        emit('status', '正在等待抖音聊天页加载，最多 75 秒…')
        config = selectors()
        succeeded = 0
        failed = 0
        for index, name in enumerate(data['friends']):
            if index:
                page.wait_for_timeout(data['interval'] * 1000)
            try:
                stage = '定位好友'
                editor = fire.open_friend(page, name, config)
                if dry_run:
                    emit('log', f'好友 {index + 1} 定位通过，未发送')
                    emit('progress', (index + 1, len(data['friends'])))
                    continue
                if data['text'].strip():
                    stage = '发送文字'
                    editor.fill(data['text'])
                    anchor = mark_outgoing(page, config)
                    fire.wait_unique(page, page.locator(config['send']), '发送按钮').click()
                    confirm_outgoing(page, config, anchor, text=data['text'])
                    emit('log', f'✓ {name}：文字发送成功（页面确认）')
                if data['image']:
                    stage = '上传图片'
                    # Image file inputs may be hidden; scope by accepted MIME.
                    uploads = image_upload_input(page, config)
                    anchor = mark_outgoing(page, config)
                    with prepared_image(data['image']) as image_path:
                        uploads.set_input_files(image_path)
                    page.wait_for_timeout(1500)
                    stage = '发送图片'
                    dialog_send = page.get_by_role('button', name='发送', exact=True)
                    buttons = [node for node in dialog_send.all() if node.is_visible()]
                    if len(buttons) == 1:
                        buttons[0].click()
                    else:
                        fire.wait_unique(page, page.locator(config['send']), '图片发送按钮').click()
                    confirm_outgoing(page, config, anchor, image=True)
                    emit('log', f'✓ {name}：表情包发送成功（页面确认）')
                succeeded += 1
                emit('progress', (index + 1, len(data['friends'])))
            except Exception as exc:
                emit('progress', (index + 1, len(data['friends'])))
                emit('progress_failed', '')
                failed += 1
                emit('log', f'✗ {name}：{stage}，' + ('发送状态未确认：' if isinstance(exc, SendUncertain) else '失败：') + str(exc))
                folder = fire.DATA / 'artifacts'
                folder.mkdir(exist_ok=True)
                path = folder / f'failure-{datetime.now():%Y%m%d-%H%M%S}.png'
                write_json(path.with_suffix('.json'), {'stage': stage, 'error_type': type(exc).__name__, 'error': str(exc), 'time': datetime.now().isoformat()})
                try:
                    page.screenshot(path=str(path), full_page=True)
                    emit('log', f'排查截图已保存：{path}')
                except Exception:
                    pass
                remaining = len(data['friends']) - index - 1
                if remaining:
                    emit('log', f'已停止本轮，剩余 {remaining} 位未执行；不会自动重发')
                break
        context.storage_state(path=str(fire.STATE), indexed_db=True)
        if not dry_run:
            emit('log', f'本轮结束：成功 {succeeded} 位，失败/未确认 {failed} 位。' + ('原因见上方红色记录。' if failed else ''))


class SendUncertain(RuntimeError):
    pass


def image_upload_input(page, config):
    if config.get('upload'):
        candidates = page.locator(config['upload'])
    else:
        # Douyin has both a new-upload input and a replace-image input.
        candidates = page.locator('input[type="file"][class~="semi-upload-hidden-input"]')
        if not candidates.count():
            candidates = page.locator('input[type="file"][accept*="image"]:not([class*="replace"]), input[type="file"][multiple][accept*=".jpg"]:not([class*="replace"])')
    if candidates.count() != 1:
        raise RuntimeError('无法唯一定位新增图片上传入口，请调整 config.json 的 selectors.upload')
    return candidates


@contextmanager
def prepared_image(path):
    # The current Douyin file input accepts GIF/JPEG/PNG, not WebP.
    if Path(path).suffix.lower() == '.webp':
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix='fire-image-') as directory:
            target = Path(directory) / 'image.png'
            with Image.open(path) as image:
                image.convert('RGBA').save(target)
            yield str(target)
    else:
        yield path


OUTGOING = '[class~="messageMessageBoxmessageBox"]:has([class*="isFromMe"])'
FAILURE = '[class*="SendStatusretry"], [class*="sendFailed"], [aria-label*="重试"], [title*="重试"]'
PENDING = '.semi-spin, [class*="im-saas-message-spin"], [data-icon="spin"]'


def mark_outgoing(page, config):
    token = uuid.uuid4().hex
    page.locator(config.get('outgoing', OUTGOING)).evaluate_all(
        '(nodes, token) => nodes.forEach(node => node.setAttribute("data-fire-before", token))', token)
    return token


def confirm_outgoing(page, config, anchor, text='', image=False, timeout=20):
    deadline = time.monotonic() + timeout
    stable_since = None
    bubble = None
    def normalize(value):
        return ' '.join(value.replace('\u200b', '').split())
    while time.monotonic() < deadline:
        candidates = page.locator(config.get('outgoing', OUTGOING))
        nodes = candidates.all()
        # Douyin virtualizes the message list. Old bubbles can remount and lose
        # our DOM marker; only data-index=0 is the newly published message.
        indexed = [(node, node.evaluate('(node) => node.closest("[data-index]")?.getAttribute("data-index") ?? null')) for node in nodes]
        if any(index is not None for node, index in indexed):
            nodes = [node for node, index in indexed if index == '0']
        fresh = [node for node in nodes if node.is_visible() and node.get_attribute('data-fire-before') != anchor]
        matching = []
        for node in fresh:
            content = node.locator('[data-e2e="msg-item-content"]')
            scope = content.first if content.count() else node
            if text and normalize(scope.inner_text()) != normalize(text):
                continue
            if image and not scope.locator('img, [class~="MessageItemImageImageBox"], [class~="MessageItemImageImage"]').count():
                continue
            matching.append(node)
        if len(matching) == 1:
            candidate = matching[0]
            if any(n.is_visible() for n in candidate.locator(FAILURE).all()) or any(n.is_visible() for n in candidate.get_by_text('发送失败', exact=True).all()):
                raise RuntimeError('抖音提示发送失败，请检查账号状态或网络')
            if any(n.is_visible() for n in candidate.locator(PENDING).all()):
                stable_since = None
            else:
                # Require the same new bubble to remain clean for 3 seconds;
                # late failure icons must not become a false success.
                identity = candidate.evaluate('(node) => { if (!node.dataset.fireConfirm) node.dataset.fireConfirm = String(Date.now()) + Math.random(); return node.dataset.fireConfirm; }')
                if identity != bubble:
                    bubble = identity
                    stable_since = None
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 3:
                    return
        else:
            stable_since = None
        page.wait_for_timeout(250)
    raise SendUncertain('未能确认新消息发送状态；为避免重复，不会自动重发')


def reject_retry(page):
    for caption in ('重试', '重新发送', '发送失败'):
        if any(n.is_visible() for n in page.get_by_text(caption, exact=True).all()):
            raise RuntimeError('抖音出现发送失败提示，已停止，不自动重发')


class TaskFooter(ttk.Frame):
    """Mascot position follows actual task progress, never elapsed time."""
    def __init__(self, parent, status):
        super().__init__(parent, padding=(24, 0, 24, 44))
        self.busy = False
        self.fraction = 0
        self.display_fraction = 0
        self.frame = 0
        self.failed = False
        self.frames = []
        self.delays = []
        self.canvas = tk.Canvas(self, width=260, height=84, bg='#faf5ec', highlightthickness=0)
        self.canvas.pack(side='left')
        ttk.Label(self, textvariable=status, wraplength=420, justify='right', anchor='e').pack(side='right', fill='x', expand=True, padx=(20, 0))
        path = ROOT / 'assets' / 'progress-mascot.gif'
        encoded = path.with_suffix('.b64')
        if path.exists() or encoded.exists():
            from PIL import Image, ImageTk, ImageSequence
            source = path if path.exists() else BytesIO(base64.b64decode(encoded.read_text(encoding='ascii')))
            with Image.open(source) as image:
                for frame in ImageSequence.Iterator(image):
                    self.frames.append(ImageTk.PhotoImage(frame.convert('RGBA').resize((56, 56), Image.Resampling.LANCZOS)))
                    self.delays.append(max(40, frame.info.get('duration', 100)))
        self.after(50, self.animate)

    def animate(self):
        self.canvas.delete('all')
        left, right, y = 20, 240, 55
        self.display_fraction += max(-0.08, min(0.08, self.fraction - self.display_fraction))
        position = self.display_fraction
        self.canvas.create_line(left, y, right, y, fill='#e8d8c1', width=8, capstyle='round')
        if position:
            self.canvas.create_line(left, y, left+(right-left)*position, y, fill='#b58d60' if not self.failed else '#b67961', width=8, capstyle='round')
        if self.frames:
            self.canvas.create_image(left+(right-left)*position, 28, image=self.frames[self.frame])
        label = f'{round(position*100)}%' + (' · 已停止' if self.failed else ' · 等待验证' if self.busy and not self.fraction else '')
        self.canvas.create_text(250, 72, text=label, anchor='e', fill='#866b5a', font=('Microsoft YaHei UI', 8))
        delay = self.delays[self.frame] if self.frames else 100
        if self.frames:
            self.frame = (self.frame+1) % len(self.frames)
        self.after(delay, self.animate)


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, state='normal', width=260, height=48):
        super().__init__(parent, width=width, height=height, bg='white', highlightthickness=0, bd=0, takefocus=True)
        self.command = command
        self.disabled = state == 'disabled'
        radius = height / 2
        points = [radius, 1, width-radius, 1, width-1, 1, width-1, radius, width-1, height-1, width-radius, height-1, radius, height-1, 1, height-1, 1, radius, 1, 1]
        self.shape = self.create_polygon(points, smooth=True, splinesteps=36, fill='#c6a684' if not self.disabled else '#e9deda', outline='')
        self.label = self.create_text(width/2, height/2, text=text, fill='#503c2b' if not self.disabled else '#9b8981', font=('Microsoft YaHei UI', 11))
        self.configure(cursor='arrow' if self.disabled else 'hand2')
        self.bind('<ButtonRelease-1>', lambda event: self.invoke() if 0 <= event.x <= width and 0 <= event.y <= height else None)
        self.bind('<Return>', lambda event: self.invoke())
        self.bind('<space>', lambda event: self.invoke())
        self.bind('<Enter>', lambda event: self.itemconfigure(self.shape, fill='#b28c65') if not self.disabled else None)
        self.bind('<Leave>', lambda event: self.itemconfigure(self.shape, fill='#c6a684') if not self.disabled else None)

    def invoke(self):
        if not self.disabled:
            self.command()


def flame_icon(master):
    image = tk.PhotoImage(master=master, width=64, height=64)
    outer = [(32, 2), (35, 17), (43, 12), (45, 29), (54, 39), (52, 52), (43, 61), (25, 63), (13, 55), (9, 44), (12, 31), (21, 22), (25, 10), (26, 26)]
    inner = [(32, 31), (35, 42), (40, 38), (43, 49), (39, 57), (30, 60), (22, 55), (21, 47), (27, 41)]
    def inside(x, y, polygon):
        result = False
        previous = polygon[-1]
        for current in polygon:
            ax, ay = previous
            bx, by = current
            if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
                result = not result
            previous = current
        return result
    for y in range(64):
        for x in range(64):
            if inside(x+.5, y+.5, outer):
                image.put('#ffd080' if inside(x+.5, y+.5, inner) else '#ef704a', (x, y))
    return image


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('fire · 火宝')
        self.flame_icon = flame_icon(self)
        self.iconphoto(True, self.flame_icon)
        self.geometry('800x930')
        self.minsize(800, 930)
        self.configure(bg='#faf5ec')
        self.events = queue.Queue()
        self.tray_icon = None
        self.tray_starting = False
        self.jobs = queue.Queue()
        threading.Thread(target=self.browser_worker, daemon=True).start()
        self.busy = False
        self.logged_in = False
        self.on_dashboard = False
        self.enabled = False
        self.saved = None
        fire.AUTH.mkdir(exist_ok=True)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#faf5ec')
        style.configure('TLabel', background='#faf5ec', foreground='#584331')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 25, 'bold'))
        style.configure('Accent.TButton', background='#c6a684', foreground='#503c2b', padding=(20, 12))
        style.configure('TButton', padding=(12, 7), background='#e6d6bf', foreground='#584331', borderwidth=0)
        style.map('TButton', background=[('active', '#d9c3a6'), ('disabled', '#eee4d6')], foreground=[('disabled', '#ac9a87')])
        style.configure('TEntry', fieldbackground='#fffdf8', foreground='#584331', bordercolor='#d4bea2')
        style.configure('TCheckbutton', background='#f0e6d7', foreground='#584331')
        style.map('TCheckbutton', background=[('active', '#e7d8c2')])
        style.configure('Vertical.TScrollbar', background='#d9c3a6', troughcolor='#f3eadc', borderwidth=0)
        style.configure('Login.TFrame', background='white')
        style.configure('Login.TLabel', background='white', foreground='#76645d')
        style.configure('LoginTitle.TLabel', background='white', foreground='#694e43', font=('Microsoft YaHei UI', 28, 'bold'))
        style.configure('Login.TButton', background='#c6a684', foreground='#503c2b', padding=(20, 12), font=('Microsoft YaHei UI', 11))
        style.map('Login.TButton', background=[('disabled', '#e9deda'), ('active', '#b28c65')], foreground=[('disabled', '#9b8981')])
        self.status = tk.StringVar(value='欢迎使用 fire')
        self.container = ttk.Frame(self, padding=28)
        self.container.pack(fill='both', expand=True)
        self.footer = TaskFooter(self, self.status)
        self.footer.pack(fill='x')
        self.login_view()
        self.after(150, self.poll)
        self.after(1000, self.tick)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def emit(self, kind, value):
        self.events.put((kind, value))

    def clear(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def login_view(self):
        self.on_dashboard = False
        self.clear()
        canvas = tk.Canvas(self.container, background='white', highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        background = ROOT / 'assets' / 'login-background.png'
        image_item = None
        self.login_image = None
        encoded_background = background.with_suffix('.b64')
        if background.exists() or encoded_background.exists():
            original = tk.PhotoImage(file=str(background)) if background.exists() else tk.PhotoImage(data=encoded_background.read_text(encoding='ascii'))
            self.login_image_variants = [original, original.zoom(4).subsample(5), original.zoom(3).subsample(5), original.subsample(2), original.subsample(3), original.subsample(6)]
            self.login_image = original
            image_item = canvas.create_image(0, 0, image=self.login_image, anchor='s')
        content = ttk.Frame(canvas, style='Login.TFrame')
        content_item = canvas.create_window(0, 20, window=content, anchor='n', width=360)
        profile = read_json(PROFILE, {})
        saved = fire.STATE.exists()
        self.login_heading = tk.Canvas(content, width=360, height=252, bg='white', highlightthickness=0)
        self.login_heading.pack(pady=(0, 12))
        self.login_heading.create_oval(60, 0, 300, 240, fill='#eee1d2', outline='')
        self.login_heading.create_oval(76, 16, 284, 224, fill='#f7eee4', outline='')
        self.login_heading.create_text(180, 68, text='fire', fill='#694e43', font=('Microsoft YaHei UI', 27, 'bold'))
        self.login_heading.create_text(180, 123, text='让每天的问候，按时到达', width=200, justify='center', fill='#866b5a', font=('Microsoft YaHei UI', 10))
        self.login_heading.create_text(180, 175, text='已保存账号：' + profile.get('name', '待验证账号') if saved else '首次使用，请扫码登录', width=180, justify='center', fill='#866b5a', font=('Microsoft YaHei UI', 9))
        self.saved_login_button = RoundedButton(content, text='登录已保存账号', command=lambda: self.start_login(True), state='normal' if saved else 'disabled')
        self.saved_login_button.pack(pady=(0, 10))
        self.qr_login_button = RoundedButton(content, text='扫码登录 / 切换账号', command=lambda: self.start_login(False))
        self.qr_login_button.pack()
        ttk.Label(content, text='已保存账号可快速进入\n首次登录或切换账号时扫码', style='Login.TLabel', justify='center').pack(pady=(14, 0))
        def layout(event):
            canvas.coords(content_item, event.width / 2, 18)
            canvas.itemconfigure(content_item, width=min(360, max(240, event.width - 48)))
            if image_item is not None:
                available = max(0, event.height - content.winfo_reqheight() - 46)
                self.login_image = next((picture for picture in self.login_image_variants if picture.height() <= available), self.login_image_variants[-1])
                canvas.itemconfigure(image_item, image=self.login_image)
                canvas.coords(image_item, event.width / 2, event.height - 8)
        canvas.bind('<Configure>', layout)

    def launch(self, operation, *args):
        if self.busy:
            messagebox.showinfo('任务进行中', '请等待当前操作结束')
            return
        self.busy = True
        self.footer.busy = True
        self.footer.fraction = 0
        self.footer.display_fraction = 0
        self.footer.failed = False
        self.jobs.put((operation, args))

    def browser_worker(self):
        WORKER_STATE.cache_enabled = True
        while True:
            job = self.jobs.get()
            if job is None:
                close_cached_browser()
                return
            operation, args = job
            try:
                operation(*args, self.emit)
            except Exception as exc:
                self.emit('progress_failed', '')
                kind = 'log' if operation is send_round else 'saved_login_error' if operation is browser_login and args[0] else 'error'
                self.emit(kind, '发送失败：' + str(exc) if operation is send_round else str(exc))
            finally:
                self.emit('idle', '')

    def start_login(self, reuse):
        if self.busy:
            self.status.set('正在验证账号，请稍候；可以先编辑好友和消息')
            return
        if reuse and fire.STATE.exists():
            self.logged_in = False
            if not self.on_dashboard:
                name = read_json(PROFILE, {}).get('name', '已保存账号')
                self.dashboard(name)
            self.account_status.set('后台验证中，可先编辑设置')
            self.send_button.config(state='disabled')
            self.status.set('已进入设置页，正在后台验证账号…')
        self.launch(browser_login, reuse)

    def dashboard(self, name):
        self.clear()
        self.on_dashboard = True
        self.account_name = tk.StringVar(value=name)
        self.account_status = tk.StringVar(value='已登录' if self.logged_in else '待验证')
        header = ttk.Frame(self.container)
        header.pack(fill='x')
        self.avatar_canvas = tk.Canvas(header, width=60, height=60, bg='#faf5ec', highlightthickness=0)
        self.avatar_canvas.pack(side='left')
        self.show_avatar(read_json(PROFILE, {}).get('avatar', ''))
        ttk.Button(header, text='切换账号', command=self.switch).pack(side='right')
        ttk.Button(header, text='重新验证', command=lambda: self.start_login(True)).pack(side='right', padx=8)
        account_line = ttk.Frame(self.container)
        account_line.pack(fill='x', pady=(4, 10))
        ttk.Label(account_line, text='当前账号：').pack(side='left')
        ttk.Label(account_line, textvariable=self.account_name).pack(side='left')
        ttk.Label(account_line, textvariable=self.account_status).pack(side='left', padx=12)
        settings = read_json(SETTINGS, {})
        # Migrate previous free-text names while retaining unchecked entries.
        self.friend_items = settings.get('friend_items', [
            {'name': name, 'selected': True} for name in settings.get('friends', [])
        ])[:20]
        columns = ttk.Frame(self.container, height=320)
        columns.pack(fill='both', expand=True)
        columns.pack_propagate(False)
        left = ttk.Frame(columns, width=310)
        left.pack(side='left', fill='both', padx=(0, 22))
        left.pack_propagate(False)
        right = ttk.Frame(columns)
        right.pack(side='left', fill='both', expand=True)
        heading = ttk.Frame(left)
        heading.pack(fill='x', pady=5)
        ttk.Label(heading, text='续火花好友', font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        self.friend_count = tk.StringVar()
        ttk.Label(heading, textvariable=self.friend_count).pack(side='right')
        add = ttk.Frame(left)
        add.pack(fill='x', pady=(6, 12))
        self.new_friend = tk.StringVar()
        entry = ttk.Entry(add, textvariable=self.new_friend)
        entry.pack(side='left', fill='both', expand=True, padx=(0, 6))
        entry.bind('<Return>', lambda event: self.add_friend())
        ttk.Button(add, text='添加好友', command=self.add_friend).pack(side='right')
        box = ttk.Frame(left)
        box.pack(fill='both', expand=True, pady=(0, 14))
        self.friend_canvas = tk.Canvas(box, background='white', highlightthickness=0)
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.friend_canvas.yview)
        scroll.pack(side='right', fill='y')
        self.friend_canvas.pack(side='left', fill='both', expand=True)
        self.friend_canvas.configure(yscrollcommand=scroll.set)
        self.friend_rows = ttk.Frame(self.friend_canvas)
        self.friend_window = self.friend_canvas.create_window((0, 0), window=self.friend_rows, anchor='nw')
        self.friend_rows.bind('<Configure>', lambda event: self.friend_canvas.configure(scrollregion=self.friend_canvas.bbox('all')))
        self.friend_canvas.bind('<Configure>', lambda event: self.friend_canvas.itemconfigure(self.friend_window, width=event.width))
        self.friend_canvas.bind('<MouseWheel>', self.scroll_friends)
        ttk.Label(left, text='勾选后才会发送 · 重名请设置唯一备注', wraplength=300).pack(side='bottom', anchor='w', pady=(0, 4), before=box)
        self.render_friends()
        ttk.Label(right, text='发送内容', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=5)
        self.text = tk.Text(right, height=1, width=35, wrap='none', font=('Microsoft YaHei UI', 11), relief='flat', bg='#fffdf8', fg='#584331', padx=10, pady=6)
        self.text.pack(fill='x', pady=(6, 8))
        self.emoji_images = {}
        self.emoji_instances = {}
        for index, emoji in enumerate(('🔥', '❤️', '😊', '🌹', '👍')):
            path = ROOT / 'assets' / 'emojis' / f'{index}.png'
            encoded = path.with_suffix('.b64')
            if path.exists() or encoded.exists():
                image = tk.PhotoImage(file=str(path)) if path.exists() else tk.PhotoImage(data=encoded.read_text(encoding='ascii'))
                self.emoji_images[emoji] = image.subsample(2)
        self.insert_message(settings.get('text', '续火花 🔥'))
        emojis = ttk.Frame(right)
        emojis.pack(fill='x', pady=5)
        for emoji in ('🔥', '❤️', '😊', '🌹', '👍'):
            ttk.Button(emojis, image=self.emoji_images.get(emoji, ''), text='' if emoji in self.emoji_images else emoji, width=3, command=lambda e=emoji: self.insert_emoji(e)).pack(side='left', padx=2)
        self.image = tk.StringVar(value=settings.get('image', ''))
        self.preview = tk.Canvas(right, height=116, bg='#fffdf8', highlightbackground='#dfcdb6', highlightthickness=1)
        self.preview.pack(fill='both', expand=True, pady=(5, 6))
        image_actions = ttk.Frame(right)
        image_actions.pack(fill='x')
        ttk.Button(image_actions, text='选择表情包图片', command=self.choose_image).pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Button(image_actions, text='清除图片', command=lambda: self.image.set('')).pack(side='right')
        self.image_name = tk.StringVar()
        ttk.Label(right, textvariable=self.image_name, wraplength=350, font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=4)
        self.image.trace_add('write', lambda *args: self.update_preview())
        self.preview.bind('<Configure>', lambda event: self.update_preview())
        self.update_preview()
        ttk.Label(right, text='支持文字、Emoji 和 PNG / JPG / GIF / WebP 图片', wraplength=350, font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=4)
        self.clock = tk.StringVar(value=settings.get('time', '09:00'))
        self.interval = tk.StringVar(value=str(settings.get('interval', 10)))
        ttk.Label(self.container, text='运行记录').pack(anchor='w', pady=(12, 5))
        self.logs = tk.Text(self.container, height=4, state='disabled', font=('Microsoft YaHei UI', 9), relief='flat', bg='#fffdf8', fg='#584331')
        self.logs.tag_configure('success', foreground='#168047')
        self.logs.tag_configure('failure', foreground='#c83246')
        self.logs.tag_configure('uncertain', foreground='#a66c00')
        self.logs.pack(fill='x')
        bottom = ttk.Frame(self.container)
        bottom.pack(fill='x', pady=(16, 0))
        self.schedule_summary = tk.StringVar(value='每日 ' + self.clock.get() + ' · 计划未开启')
        ttk.Label(bottom, textvariable=self.schedule_summary).pack(side='left')
        self.send_button = ttk.Button(bottom, text='立即发送', style='Accent.TButton', command=lambda: self.execute(False), state='normal' if self.logged_in else 'disabled')
        self.send_button.pack(side='right', anchor='n', padx=(10, 0))
        ttk.Button(bottom, text='发送时间设定', command=self.time_dialog).pack(side='right', anchor='n')

    def insert_emoji(self, emoji):
        if self.text.tag_ranges('sel'):
            self.text.delete('sel.first', 'sel.last')
        if emoji in self.emoji_images:
            name = 'emoji-' + uuid.uuid4().hex
            self.text.image_create('insert', image=self.emoji_images[emoji], name=name, align='center')
            self.emoji_instances[name] = emoji
        else:
            self.text.insert('insert', emoji)
        self.text.focus_set()

    def insert_message(self, value):
        index = 0
        while index < len(value):
            match = next((emoji for emoji in self.emoji_images if value.startswith(emoji, index)), None)
            if match:
                self.insert_emoji(match)
                index += len(match)
            else:
                self.text.insert('insert', value[index])
                index += 1

    def message_content(self):
        return ''.join(value if kind == 'text' else self.emoji_instances.get(value, '') for kind, value, index in self.text.dump('1.0', 'end-1c', text=True, image=True))

    def show_avatar(self, path):
        self.avatar_canvas.delete('all')
        self.account_avatar_image = None
        if path and Path(path).is_file():
            try:
                self.account_avatar_image = tk.PhotoImage(file=path)
                self.avatar_canvas.create_image(30, 30, image=self.account_avatar_image)
                return
            except tk.TclError:
                pass
        self.avatar_canvas.create_oval(2, 2, 58, 58, fill='#eee1d2', outline='')
        self.avatar_canvas.create_text(30, 30, text='头像', fill='#866b5a', font=('Microsoft YaHei UI', 10))

    def scroll_friends(self, event):
        self.friend_canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def render_friends(self):
        for child in self.friend_rows.winfo_children():
            child.destroy()
        self.friend_vars = []
        for index, item in enumerate(self.friend_items):
            row = ttk.Frame(self.friend_rows, padding=(8, 7))
            row.pack(fill='x')
            selected = tk.BooleanVar(value=item.get('selected', True))
            self.friend_vars.append(selected)
            check = ttk.Checkbutton(row, text=item['name'], variable=selected, command=self.persist_friends)
            check.pack(side='left', fill='both', expand=True)
            remove = ttk.Button(row, text='×', width=2, command=lambda i=index: self.remove_friend(i))
            remove.pack(side='right', fill='y')
            for widget in (row, check, remove):
                widget.bind('<MouseWheel>', self.scroll_friends)
        self.friend_count.set(f'{len(self.friend_items)} / 20')

    def persist_friends(self):
        for item, variable in zip(self.friend_items, self.friend_vars):
            item['selected'] = variable.get()
        settings = read_json(SETTINGS, {})
        settings['friend_items'] = self.friend_items
        settings['friends'] = [item['name'] for item in self.friend_items if item['selected']]
        write_json(SETTINGS, settings)
        # Immediately remove unchecked friends from an active plan.
        if self.saved is not None:
            self.saved['friends'] = settings['friends']
        if not settings['friends'] and self.enabled:
            self.enabled = False
            self.status.set('没有勾选好友，计划已暂停')
            self.update_schedule_summary()

    def add_friend(self):
        name = self.new_friend.get().strip()
        if not name:
            return
        if len(self.friend_items) >= 20:
            messagebox.showinfo('人数上限', '最多添加 20 位好友，请先移除不需要的好友')
            return
        if any(item['name'] == name for item in self.friend_items):
            messagebox.showinfo('好友已存在', '这个好友已经在列表中')
            return
        self.friend_items.append({'name': name, 'selected': True})
        self.new_friend.set('')
        self.render_friends()
        self.persist_friends()
        self.friend_canvas.update_idletasks()
        self.friend_canvas.yview_moveto(1.0)

    def remove_friend(self, index):
        self.friend_items.pop(index)
        self.render_friends()
        self.persist_friends()

    def update_schedule_summary(self):
        self.schedule_summary.set('每日 ' + self.clock.get() + (' · 计划已开启' if self.enabled else ' · 计划未开启'))

    def time_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title('发送时间设定')
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=24)
        frame.pack(fill='both', expand=True)
        clock = tk.StringVar(value=self.clock.get())
        interval = tk.StringVar(value=self.interval.get())
        active = tk.BooleanVar(value=self.enabled)
        ttk.Label(frame, text='每天发送时间（北京时间，HH:MM）').pack(anchor='w')
        ttk.Entry(frame, textvariable=clock, width=28).pack(fill='x', pady=(8, 16))
        ttk.Label(frame, text='不同好友之间的间隔（秒）').pack(anchor='w')
        ttk.Entry(frame, textvariable=interval).pack(fill='x', pady=(8, 16))
        ttk.Checkbutton(frame, text='开启每日自动发送', variable=active).pack(anchor='w')
        ttk.Label(frame, text='请保持软件开启，电脑唤醒并联网').pack(anchor='w', pady=12)
        def commit():
            if active.get() and not self.logged_in:
                messagebox.showinfo('账号尚未验证', '可以保存时间，但请等账号验证通过后再开启自动发送', parent=dialog)
                return
            previous = (self.clock.get(), self.interval.get())
            self.clock.set(clock.get())
            self.interval.set(interval.get())
            data = self.save()
            if data is None:
                self.clock.set(previous[0])
                self.interval.set(previous[1])
                return
            self.enabled = active.get()
            self.update_schedule_summary()
            dialog.destroy()
        ttk.Button(frame, text='保存时间设置', style='Accent.TButton', command=commit).pack(fill='x')

    def choose_image(self):
        path = filedialog.askopenfilename(filetypes=[('表情包图片', '*.png *.jpg *.jpeg *.gif *.webp')])
        if path:
            self.image.set(path)

    def update_preview(self):
        self.preview.delete('all')
        self.preview_image = None
        width = max(100, self.preview.winfo_width())
        height = max(60, self.preview.winfo_height())
        path = self.image.get()
        self.image_name.set(Path(path).name if path else '仅显示一张图片，重新选择会替换')
        if path and Path(path).is_file():
            try:
                from PIL import Image, ImageOps, ImageTk
                with Image.open(path) as source:
                    picture = ImageOps.contain(source.convert('RGBA'), (max(40, width-16), max(40, height-16)), Image.Resampling.LANCZOS)
                self.preview_image = ImageTk.PhotoImage(picture, master=self)
                self.preview.create_image(width/2, height/2, image=self.preview_image)
                return
            except Exception:
                pass
        self.preview.create_text(width/2, height/2, text='选择一张表情包图片' if not path else '图片无法预览，请重新选择', fill='#a28b72', width=width-20, font=('Microsoft YaHei UI', 10))

    def collect(self):
        self.persist_friends()
        return validate({'friends': [item['name'] for item in self.friend_items if item['selected']], 'friend_items': self.friend_items, 'text': self.message_content(), 'image': self.image.get(), 'interval': self.interval.get(), 'time': self.clock.get()})

    def save(self):
        try:
            data = self.collect()
            write_json(SETTINGS, data)
            self.saved = data
            self.status.set('设置已保存' + ('，每日计划使用新设置' if self.enabled else ''))
            return data
        except Exception as exc:
            messagebox.showerror('设置有误', str(exc))
            return None

    def execute(self, dry_run, data=None):
        if not self.logged_in:
            self.status.set('请等待后台登录验证通过，验证失败可点击“重新验证”或切换账号扫码')
            return
        if self.busy:
            return
        data = data or self.save()
        if data:
            self.status.set('正在检查好友…' if dry_run else '正在发送…')
            self.launch(send_round, data, dry_run)

    def toggle(self):
        if self.enabled:
            self.enabled = False
        elif self.save():
            self.enabled = True
        self.update_schedule_summary()
        self.status.set('每日计划已开启，请保持软件运行' if self.enabled else '每日计划已暂停')

    def tick(self):
        try:
            if self.enabled and self.logged_in and not self.busy and self.saved:
                key = schedule_key(datetime.now(ZoneInfo('Asia/Shanghai')), self.saved['time'])
                history = read_json(JOURNAL, {})
                if key and key not in history:
                    # Reserve before sending: crashes never cause automatic resends.
                    history[key] = '已触发；结果请查看日志，失败不自动重发'
                    write_json(JOURNAL, history)
                    self.execute(False, dict(self.saved))
        except Exception as exc:
            self.enabled = False
            if hasattr(self, 'schedule_summary'):
                self.update_schedule_summary()
            self.status.set('计划已暂停：' + str(exc))
        finally:
            self.after(1000, self.tick)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'idle':
                    self.busy = False
                    self.footer.busy = False
                elif kind == 'tray_ready':
                    self.tray_starting = False
                    self.withdraw()
                elif kind == 'tray_show':
                    self.show_window()
                elif kind == 'tray_exit':
                    self.exit_app()
                elif kind == 'progress':
                    completed, total = value
                    self.footer.fraction = completed / total if total else 0
                elif kind == 'progress_failed':
                    self.footer.failed = True
                elif kind == 'logged_in':
                    name = value['name']
                    if not name and value['reuse']:
                        name = read_json(PROFILE, {}).get('name', '')
                    if not name:
                        name = simpledialog.askstring('账号名称', '已登录，但未能读取昵称。请填写本机显示名称：', parent=self) or '已登录账号'
                    avatar = value.get('avatar', read_json(PROFILE, {}).get('avatar', '') if value.get('reuse') else '')
                    write_json(PROFILE, {'name': name, 'avatar': avatar})
                    self.logged_in = True
                    self.footer.fraction = 1
                    if self.on_dashboard:
                        self.account_name.set(name)
                        self.account_status.set('已登录')
                        self.send_button.config(state='normal')
                        self.show_avatar(avatar)
                    else:
                        self.dashboard(name)
                    self.status.set('账号验证通过，可以发送消息')
                elif kind == 'saved_login_error':
                    self.footer.failed = True
                    self.logged_in = False
                    self.enabled = False
                    if self.on_dashboard:
                        self.account_status.set('验证失败，请重新验证或扫码')
                        self.send_button.config(state='disabled')
                        self.update_schedule_summary()
                    self.status.set(value)
                    self.events.put(('log', '登录验证失败：' + value))
                elif kind == 'error':
                    self.footer.failed = True
                    self.status.set('操作失败：' + value)
                    messagebox.showerror('操作失败', value)
                elif kind == 'log':
                    self.logs.config(state='normal')
                    tag = 'uncertain' if '未确认' in value else 'failure' if '失败' in value or value.startswith('✗') else 'success' if value.startswith('✓') else ''
                    self.logs.insert('end', datetime.now().strftime('%H:%M:%S ') + value + '\n', tag)
                    self.logs.see('end')
                    self.logs.config(state='disabled')
                    self.status.set(value)
                else:
                    self.status.set(value)
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def switch(self):
        if self.busy:
            messagebox.showinfo('任务进行中', '请等待当前任务结束再切换账号')
            return
        self.enabled = False
        self.logged_in = False
        self.login_view()

    def destroy(self):
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.jobs.put(None)
        # Tk callbacks must not outlive a window, including in GUI tests.
        for callback in self.tk.call('after', 'info'):
            try:
                self.tk.call('after', 'cancel', callback)
            except tk.TclError:
                pass
        super().destroy()

    def close(self):
        if self.tray_icon is not None and not self.tray_starting:
            self.withdraw()
            return
        if self.tray_starting:
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new('RGBA', (64, 64))
            draw = ImageDraw.Draw(image)
            draw.polygon([(32, 2), (35, 17), (43, 12), (45, 29), (54, 39), (52, 52), (43, 61), (25, 63), (13, 55), (9, 44), (12, 31), (21, 22), (25, 10), (26, 26)], fill='#ef704a')
            draw.polygon([(32, 31), (35, 42), (40, 38), (43, 49), (39, 57), (30, 60), (22, 55), (21, 47), (27, 41)], fill='#ffd080')
            self.tray_icon = pystray.Icon('fire', image, '火宝 · 后台运行', menu=pystray.Menu(
                pystray.MenuItem('打开火宝', lambda icon, item: self.emit('tray_show', ''), default=True),
                pystray.MenuItem('退出软件', lambda icon, item: self.emit('tray_exit', ''))))
            self.tray_starting = True
            def ready(icon):
                icon.visible = True
                self.emit('tray_ready', '')
            self.tray_icon.run_detached(setup=ready)
        except Exception as exc:
            self.tray_starting = False
            self.tray_icon = None
            messagebox.showerror('无法进入后台', f'系统托盘启动失败，窗口将保持打开：{exc}', parent=self)

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def exit_app(self):
        if self.enabled or self.busy:
            self.show_window()
            if not messagebox.askyesno('退出火宝', '退出会停止每日计划；正在发送的结果可能不确定。确定退出？', parent=self):
                return
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
