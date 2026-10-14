"""Offline browser smoke test: does not visit Douyin or send a real message."""
from unittest.mock import patch
import tempfile
from pathlib import Path
import gui
import fire

HTML = '''<input placeholder="搜索"><div class="SearchPanelitembox"><span>测试好友</span><button onclick="document.querySelector('h2').textContent='测试好友';document.querySelector('h3').textContent='测试好友'">发消息</button></div><h2 class="RightPanelHeadertitle">未选择</h2><h3 class="RightPanelHeaderTitle">未选择</h3><div contenteditable="true"></div><input type="file" accept="image/*"><button onclick="publish()">发送</button><script>
function publish() {
const editor = document.querySelector('[contenteditable]');
const bubble = document.createElement('section');
bubble.className = 'messageMessageBoxmessageBox';
const content = document.createElement('div');
content.className = 'messageMessageBoxisFromMe';
content.setAttribute('data-e2e', 'msg-item-content');
if (editor.textContent) content.textContent = editor.textContent;
else if (document.querySelector('input[type=file]').files.length) { const image=document.createElement('img');image.width=30;image.height=30;content.append(image); }
bubble.append(content); document.body.append(bubble);
editor.textContent='';document.body.dataset.sent=Number(document.body.dataset.sent||0)+1;
}
</script>'''


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = gui.launch_browser(p, headless=True)
        try:
            page = browser.new_page()
            page.set_content('<input type="file" multiple accept=".jpg,.jpeg,.png,.gif,.mp4" class="semi-upload-hidden-input"><input type="file" accept=".jpg,.jpeg,.png,.gif,.mp4" class="semi-upload-hidden-input-replace">')
            upload = gui.image_upload_input(page, {})
            assert upload.get_attribute('class') == 'semi-upload-hidden-input'
            page.set_content('<div>loading</div><script>setTimeout(() => document.body.innerHTML = \'<input aria-label="搜索好友">\', 1000)</script>')
            assert fire.wait_chat_ready(page, gui.selectors()['search'], timeout=5).is_visible()
            page.set_content(HTML)
            page.evaluate('''() => {
                const title=document.querySelector('h2');
                const wrapper=document.createElement('div');wrapper.className='RightPanelHeadertitleContainer';
                title.before(wrapper);wrapper.append(title);
                const badge=document.createElement('span');badge.textContent='321';wrapper.append(badge);
                const bar=document.createElement('div');bar.className='RightPanelHeadertitleBarContainer';
                bar.innerHTML='<svg class="RightPanelHeadertitleIcon" width="20" height="20"></svg>';document.body.append(bar);
            }''')
            editor = fire.open_friend(page, '测试好友', gui.selectors())
            legacy = dict(gui.selectors(), header="[class*='RightPanelHeadertitle'], [class*='RightPanelHeaderTitle']")
            assert fire.open_friend(page, '测试好友', legacy).is_visible()
            editor.fill('本地测试 🔥')
            page.get_by_role('button', name='发送', exact=True).click()
            assert editor.inner_text() == ''
            assert page.locator('body').get_attribute('data-sent') == '1'
            # Verify image input works without a network upload.
            page.locator('input[type=file]').set_input_files({'name': 'test.gif', 'mimeType': 'image/gif', 'buffer': b'GIF89a'})
            assert page.locator('input[type=file]').evaluate('(node) => node.files[0].name') == 'test.gif'
            # Identical historical text cannot confirm a new send.
            anchor = gui.mark_outgoing(page, gui.selectors())
            try:
                gui.confirm_outgoing(page, gui.selectors(), anchor, text='本地测试 🔥', timeout=0.6)
                raise AssertionError('old bubble was accepted')
            except gui.SendUncertain:
                pass
            # A delayed failure icon must win over an initially clean bubble.
            page.evaluate('''() => {
                const bubble = document.createElement('section');
                bubble.className='messageMessageBoxmessageBox';
                bubble.innerHTML='<div class="messageMessageBoxisFromMe" data-e2e="msg-item-content">late</div>';
                document.body.append(bubble);
                setTimeout(() => { const retry=document.createElement('span');retry.className='ContentSideSendStatusretry';retry.textContent='!';bubble.append(retry); }, 700);
            }''')
            try:
                gui.confirm_outgoing(page, gui.selectors(), anchor, text='late', timeout=3)
                raise AssertionError('late failure was accepted')
            except RuntimeError as exc:
                assert '发送失败' in str(exc)
            # Real Douyin image messages can use a DIV, not an IMG; remounted
            # history must not create multiple candidates or fake confirmation.
            page.set_content('<div data-index="1"><section class="messageMessageBoxmessageBox"><div class="messageMessageBoxisFromMe" data-e2e="msg-item-content"><div class="MessageItemImageImageBox" style="width:30px;height:30px"></div></div></section></div>')
            anchor = gui.mark_outgoing(page, {})
            page.evaluate('''() => {
                document.querySelector('[data-fire-before]').removeAttribute('data-fire-before');
                const row=document.createElement('div');row.dataset.index='0';
                row.innerHTML='<section class="messageMessageBoxmessageBox"><div class="messageMessageBoxisFromMe" data-e2e="msg-item-content"><div class="MessageItemImageImageBox" style="width:30px;height:30px"></div></div></section>';
                document.body.append(row);
            }''')
            gui.confirm_outgoing(page, {}, anchor, image=True, timeout=5)
        finally:
            browser.close()
    # Full orchestration against a local HTML fixture.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fixture = root / 'chat.html'
        fixture.write_text(HTML, encoding='utf-8')
        state = root / 'state.json'
        state.write_text('{"cookies": [], "origins": []}', encoding='utf-8')
        image = root / 'test.gif'
        image.write_bytes(b'GIF89a')
        data = dict(friends=['测试好友'], text='offline', image=str(image), interval=0, time='09:00')
        events = []
        with patch.object(fire, 'URL', fixture.as_uri()), patch.object(fire, 'STATE', state):
            gui.send_round(data, True, lambda *event: events.append(event))
            logs = [value for kind, value in events if kind == 'log']
            assert len(logs) == 1 and '未发送' in logs[0]
            events.clear()
            gui.send_round(data, False, lambda *event: events.append(event))
            logs = [value for kind, value in events if kind == 'log']
            assert len(logs) == 3 and '文字发送成功' in logs[0] and '表情包发送成功' in logs[1] and '成功 1 位' in logs[2], logs
    print('PASS: offline recipient search, text submission, image selection, dry-run and full send orchestration')


if __name__ == '__main__':
    main()
