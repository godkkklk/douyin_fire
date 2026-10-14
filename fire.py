"""Local Douyin chat automation. Run --help for commands."""
import argparse
import json
import math
import random
import sys
import os
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'HuoBao' if getattr(sys, 'frozen', False) else ROOT
DATA.mkdir(parents=True, exist_ok=True)
AUTH = DATA / "auth"
STATE = AUTH / "state.json"
URL = "https://www.douyin.com/chat"


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("friends", "messages"):
        values = config.get(key)
        if not isinstance(values, list) or not values or any(
            not isinstance(v, str) or not v.strip() for v in values
        ):
            raise ValueError(f"{key} 必须是非空字符串列表")
    if len(set(config["friends"])) != len(config["friends"]):
        raise ValueError("好友列表不能重复")
    interval = config.get("interval_seconds", 10)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 0:
        raise ValueError("interval_seconds 必须为非负有限数字")
    if not isinstance(config.get("headless", False), bool):
        raise ValueError("headless 必须为 true 或 false")
    defaults = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    overrides = config.get("selectors", {})
    if not isinstance(overrides, dict) or any(not isinstance(v, str) or not v.strip() for v in overrides.values()):
        raise ValueError("selectors 必须包含非空 CSS 选择器字符串")
    config["selectors"] = {**defaults["selectors"], **overrides}
    config["interval_seconds"] = interval
    return config


def unique_visible(locator, description):
    matches = [node for node in locator.all() if node.is_visible()]
    if len(matches) != 1:
        raise RuntimeError(f"{description}需要唯一可见匹配，实际 {len(matches)} 个；请检查页面或调整 selectors")
    return matches[0]


def wait_unique(page, locator, description, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return unique_visible(locator, description)
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            page.wait_for_timeout(250)


def wait_chat_ready(page, search_selector, timeout=75):
    """DOMContentLoaded does not mean the Douyin chat SPA has hydrated."""
    deadline = time.monotonic() + timeout
    while True:
        if page.is_closed():
            raise RuntimeError('抖音浏览器已关闭，请重新操作')
        matches = [node for node in page.locator(search_selector).all() if node.is_visible()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError('检测到多个搜索框，停止定位以避免发错好友')
        for caption in ('扫码登录', '验证码登录', '安全验证', '验证身份'):
            if any(node.is_visible() for node in page.get_by_text(caption, exact=True).all()):
                raise RuntimeError('抖音需要重新登录或安全验证，请回到登录页完成验证后重试')
        if time.monotonic() >= deadline:
            raise RuntimeError('抖音聊天页加载超时（等待 75 秒仍未出现搜索框）。若浏览器一直是灰色占位，请检查网络或重新扫码登录；本次未发送消息。')
        page.wait_for_timeout(500)


def login(browser):
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        input("请在浏览器选择扫码登录，完成验证并进入私信页面后，在这里按回车：")
        cookies = context.cookies("https://www.douyin.com")
        if not any(c["name"] in ("sessionid", "sessionid_ss") and c["value"] for c in cookies):
            raise RuntimeError("没有检测到登录会话 Cookie，请完成登录后重试")
        AUTH.mkdir(exist_ok=True)
        context.storage_state(path=str(STATE), indexed_db=True)
        (AUTH / "cookies.json").write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已保存登录状态到 auth/state.json，Cookie 到 auth/cookies.json。请勿分享这些文件。")
    finally:
        context.close()


def open_friend(page, name, selectors):
    search = wait_chat_ready(page, selectors["search"])
    search.fill(name)
    rows = page.locator(selectors["result"]).filter(has=page.get_by_text(name, exact=True))
    try:
        row = wait_unique(page, rows, "好友搜索结果")
    except RuntimeError as exc:
        for caption in ('未搜索到相关内容', '暂无搜索结果', '未找到相关用户'):
            if any(node.is_visible() for node in page.get_by_text(caption, exact=True).all()):
                raise RuntimeError('抖音网页未搜索到这位好友，请核对完整昵称或备注，并先在抖音网页版确认能搜索到；未发送消息。') from exc
        raise RuntimeError('无法唯一确认好友搜索结果，请检查完整昵称或备注、重名情况及页面是否加载完成；未发送消息。') from exc
    action = row.get_by_text("发消息", exact=True)
    if action.count() and action.first.is_visible():
        unique_visible(action, "发消息入口").click()
    else:
        row.click()
    # Some builds show the same nickname in two header nodes. Inspect ALL
    # visible titles rather than accepting the first partial text match.
    deadline = time.monotonic() + 15
    while True:
        nodes = [node for node in page.locator(selectors['header']).all() if node.is_visible() and node.evaluate('(node) => node instanceof HTMLElement')]
        # Ignore wrapping containers matched by legacy custom selectors. Only
        # leaf title nodes can represent the recipient, never icons or badges.
        leaves = [node for node in nodes if not node.evaluate('(node, selector) => !!node.querySelector(selector)', selectors['header'])]
        titles = [node.inner_text().strip() for node in leaves if node.inner_text().strip()]
        if titles and all(title == name for title in titles):
            return wait_unique(page, page.locator(selectors['editor']), '消息输入框')
        if time.monotonic() >= deadline:
            raise RuntimeError('当前聊天标题与目标好友不完全一致，或尚未加载，已停止发送')
        page.wait_for_timeout(250)


def run(browser, config, dry_run):
    if not STATE.exists():
        raise RuntimeError("请先运行 python fire.py login 扫码登录")
    context = browser.new_context(storage_state=str(STATE))
    failed = 0
    try:
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        for index, friend in enumerate(config["friends"]):
            if index:
                page.wait_for_timeout(config["interval_seconds"] * 1000)
            try:
                editor = open_friend(page, friend, config["selectors"])
                if dry_run:
                    print(f"[检查通过] 第 {index + 1} 位好友定位成功，未发送")
                    continue
                message = random.choice(config["messages"])
                editor.fill(message)
                send = wait_unique(page, page.locator(config["selectors"]["send"]), "发送按钮")
                send.click()
                page.wait_for_timeout(3000)
                # A cleared editor is only evidence of submission, not delivery.
                if editor.inner_text().strip():
                    raise RuntimeError("点击后输入框仍有内容，无法确认提交")
                if page.get_by_text("重试", exact=True).filter(visible=True).count():
                    raise RuntimeError("页面出现重试提示，消息可能发送失败")
                print(f"[已提交，送达未核实] 第 {index + 1} 位好友；请在抖音确认结果")
            except Exception as exc:
                failed += 1
                folder = ROOT / "artifacts"
                folder.mkdir(exist_ok=True)
                screenshot = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{index + 1}.png"
                try:
                    page.screenshot(path=str(screenshot), full_page=True)
                    print(f"失败截图：{screenshot}")
                except Exception:
                    pass
                print(f"[失败] 第 {index + 1} 位好友：{type(exc).__name__}: {exc}")
                # Stop after ambiguous send state to avoid accidental duplicates.
                break
        if not failed:
            context.storage_state(path=str(STATE), indexed_db=True)
    finally:
        context.close()
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="fire：扫码登录、配置和抖音私信发送")
    parser.add_argument("command", choices=["login", "configure", "run", "check"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "configure":
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["friends"] = [v.strip() for v in input("好友完整名称（多个用英文逗号分隔）：").split(",") if v.strip()]
        config["messages"] = [input("发送文字内容：")]
        config["interval_seconds"] = float(input("好友之间发送间隔秒数（例如 10）："))
        # Validate before replacing an existing configuration.
        temp = args.config.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            load_config(temp)
            temp.replace(args.config)
        finally:
            temp.unlink(missing_ok=True)
        print(f"配置已保存：{args.config}")
        return 0
    config = None if args.command == "login" else load_config(args.config)
    if args.command == "check":
        print(f"配置有效：{len(config['friends'])} 位好友，{len(config['messages'])} 条候选消息")
        return 0
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False if config is None else config.get("headless", False))
        try:
            if args.command == "login":
                login(browser)
                return 0
            return run(browser, config, args.dry_run)
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"运行停止：{exc}", file=sys.stderr)
        sys.exit(1)
