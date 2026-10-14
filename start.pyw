"""Window-only launcher with a graphical first-run installer."""
import importlib.util
import os
import queue
import site
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)


def main():
    # Prevent two GUI processes from sending the same scheduled task.
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\FireDouyinDesktopApp')
    if kernel.GetLastError() == 183:
        window = tk.Tk()
        window.withdraw()
        messagebox.showinfo('fire', 'fire 已经在运行，请切换到现有窗口')
        window.destroy()
        return
    try:
        edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
        if not getattr(sys, 'frozen', False) and (importlib.util.find_spec('playwright') is None or importlib.util.find_spec('tzdata') is None or importlib.util.find_spec('PIL') is None or importlib.util.find_spec('pystray') is None or (not edge.exists() and not (ROOT / '.browser-ready').exists())):
            install()
        from gui import App
        App().mainloop()
    except Exception as exc:
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror('fire 启动失败', str(exc) + '\n请查看本文件夹 setup.log，或在 VS Code 中打开项目排查。')
        window.destroy()
    finally:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


def install():
    window = tk.Tk()
    window.title('fire · 首次安装')
    window.geometry('540x200')
    ttk.Label(window, text='正在准备运行环境', font=('Microsoft YaHei UI', 16)).pack(pady=20)
    status = tk.StringVar(value='首次使用需要下载 Playwright 和浏览器，请保持联网…')
    ttk.Label(window, textvariable=status, wraplength=490).pack(padx=20)
    progress = ttk.Progressbar(window, mode='indeterminate')
    progress.pack(fill='x', padx=30, pady=20)
    progress.start()
    events = queue.Queue()
    def worker():
        executable = Path(sys.executable).with_name('python.exe')
        try:
            with (ROOT / 'setup.log').open('w', encoding='utf-8') as log:
                commands = [
                    [str(executable), '-m', 'pip', 'install', '--user', '-r', str(ROOT / 'requirements.txt')],
                ]
                edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
                if not edge.exists():
                    commands.append([str(executable), '-m', 'playwright', 'install', 'chromium'])
                for command in commands:
                    result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode and 'pip' in command:
                        log.write('\nRetrying via Tsinghua PyPI mirror\n')
                        log.flush()
                        result = subprocess.run(command + ['--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode:
                        raise RuntimeError('环境下载失败，请查看 setup.log 后重试')
            (ROOT / '.browser-ready').write_text('ready', encoding='utf-8')
            events.put(None)
        except Exception as exc:
            events.put(exc)
    threading.Thread(target=worker, daemon=True).start()
    result = []
    def poll():
        try:
            result.append(events.get_nowait())
            window.destroy()
        except queue.Empty:
            window.after(200, poll)
    window.protocol('WM_DELETE_WINDOW', lambda: messagebox.showinfo('安装进行中', '请等待安装完成'))
    window.after(200, poll)
    window.mainloop()
    if result and result[0]:
        raise result[0]
    importlib.invalidate_caches()
    site.addsitedir(site.getusersitepackages())


if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        import json
        import gui
        from playwright.sync_api import sync_playwright
        window = gui.App()
        window.withdraw()
        window.update_idletasks()
        assert len(window.footer.frames) == 10
        window.destroy()
        with sync_playwright() as engine:
            browser = gui.launch_browser(engine, headless=True)
            page = browser.new_page()
            page.set_content('<title>HuoBao packaged test</title>')
            assert page.title() == 'HuoBao packaged test'
            browser.close()
        Path(sys.argv[sys.argv.index('--smoke-test') + 1]).write_text(json.dumps({'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)), 'bundled_browser': (ROOT / 'browser' / 'chrome-win64' / 'chrome.exe').exists()}), encoding='utf-8')
    else:
        main()
