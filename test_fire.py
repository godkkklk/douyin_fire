import unittest
from datetime import datetime
from unittest.mock import Mock
import fire
import gui
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

class DesktopTests(unittest.TestCase):
    def test_close_hides_to_tray_without_stopping_plan(self):
        app = gui.App()
        icon = Mock()
        try:
            app.enabled = True
            with patch('pystray.Icon', return_value=icon):
                app.close()
            setup = icon.run_detached.call_args.kwargs['setup']
            setup(icon)
            app.poll()
            self.assertEqual(app.state(), 'withdrawn')
            self.assertTrue(app.enabled)
            self.assertTrue(icon.visible)
            app.events.put(('tray_show', ''))
            app.poll()
            self.assertEqual(app.state(), 'normal')
            self.assertTrue(app.enabled)
        finally:
            app.enabled = False
            app.destroy()
        icon.stop.assert_called_once()
    def test_cached_browser_reuses_context_and_page(self):
        engine = MagicMock()
        browser = MagicMock()
        browser.is_connected.return_value = True
        browser.new_context.return_value.new_page.return_value.is_closed.return_value = False
        gui.WORKER_STATE.cache_enabled = True
        try:
            with patch('playwright.sync_api.sync_playwright') as factory, patch.object(gui, 'launch_browser', return_value=browser) as launch:
                factory.return_value.start.return_value = engine
                with gui.browser_scope() as first:
                    self.assertTrue(first[3])
                with gui.browser_scope() as second:
                    self.assertFalse(second[3])
                    self.assertIs(first[2], second[2])
                launch.assert_called_once()
                browser.new_context.assert_called_once()
                browser.close.assert_not_called()
                gui.close_cached_browser()
                browser.close.assert_called_once()
                engine.stop.assert_called_once()
        finally:
            gui.WORKER_STATE.cache_enabled = False
            gui.WORKER_STATE.browser = None
    def test_webp_is_converted_for_douyin_upload(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.webp'
            Image.new('RGB', (12, 12), '#cfae90').save(source)
            with gui.prepared_image(str(source)) as path:
                self.assertEqual(Path(path).suffix, '.png')
                with Image.open(path) as image:
                    self.assertEqual(image.format, 'PNG')
    def test_avatar_download_and_round_cache(self):
        from PIL import Image
        from io import BytesIO
        buffer = BytesIO()
        Image.new('RGB', (100, 80), '#cfae90').save(buffer, format='JPEG')
        context = Mock()
        context.request.get.return_value.ok = True
        context.request.get.return_value.body.return_value = buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch.object(fire, 'AUTH', Path(directory)), patch.object(gui, 'PROFILE', Path(directory) / 'profile.json'):
            path = gui.cache_avatar(context, {'avatar_url': 'https://example.com/avatar.jpg'}, False)
            with Image.open(path) as image:
                self.assertEqual(image.size, (56, 56))
                self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_dashboard_bottom_buttons_visible_at_minimum_size(self):
        app = gui.App()
        try:
            app.dashboard('测试账号')
            app.geometry('820x680')
            app.update()
            button = app.send_button
            self.assertLess(button.winfo_rooty() + button.winfo_height(), app.winfo_rooty() + app.winfo_height())
            self.assertTrue(app.avatar_canvas.winfo_ismapped())
            self.assertEqual(len(app.footer.frames), 10)
            for fraction in (0, 1):
                app.footer.busy = False
                app.footer.fraction = fraction
                previous = app.footer.frame
                app.footer.animate()
                self.assertNotEqual(previous, app.footer.frame)
            app.events.put(('progress', (1, 2)))
            app.poll()
            self.assertEqual(app.footer.fraction, 0.5)
            app.events.put(('progress_failed', ''))
            app.poll()
            self.assertTrue(app.footer.failed)
            app.text.delete('1.0', 'end')
            app.insert_message('测试🔥❤️😊🌹👍结束')
            self.assertEqual(app.message_content(), '测试🔥❤️😊🌹👍结束')
            self.assertEqual(len(app.text.image_names()), 5)
            self.assertEqual(int(app.text.cget('height')), 1)
            app.image.set(str(gui.ROOT / 'assets' / 'login-background.png'))
            app.update()
            self.assertIsNotNone(app.preview_image)
            self.assertEqual(len(app.preview.find_all()), 1)
        finally:
            app.destroy()

    def test_login_buttons_equal_size_and_background_does_not_overlap(self):
        app = gui.App()
        try:
            for dimensions in ('940x760', '820x680'):
                app.geometry(dimensions)
                app.update()
                first, second = app.saved_login_button, app.qr_login_button
                self.assertEqual((first.winfo_width(), first.winfo_height()), (second.winfo_width(), second.winfo_height()))
                self.assertEqual((second.winfo_width(), second.winfo_height()), (260, 48))
                circles = [item for item in app.login_heading.find_all() if app.login_heading.type(item) == 'oval']
                self.assertEqual(len(circles), 2)
                self.assertTrue(all(app.login_heading.itemcget(item, 'outline') == '' for item in circles))
                content = second.master
                canvas = content.master
                self.assertLess(content.winfo_y() + content.winfo_height(), canvas.winfo_height() - 8 - app.login_image.height())
        finally:
            app.destroy()

    def test_saved_account_enters_settings_before_verification(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, 'SETTINGS', Path(directory) / 'settings.json'), patch.object(gui, 'PROFILE', Path(directory) / 'profile.json'), patch.object(fire, 'STATE', MagicMock()):
            app = gui.App()
            app.withdraw()
            app.launch = Mock()
            try:
                app.start_login(True)
                self.assertTrue(app.on_dashboard)
                self.assertFalse(app.logged_in)
                self.assertIn('disabled', app.send_button.state())
                app.text.delete('1.0', 'end')
                app.text.insert('1.0', '验证期间编辑的内容')
                app.execute(False)
                app.launch.assert_called_once_with(gui.browser_login, True)
                app.events.put(('logged_in', {'name': '账号昵称', 'reuse': True}))
                app.poll()
                self.assertTrue(app.logged_in)
                self.assertNotIn('disabled', app.send_button.state())
                self.assertEqual(app.text.get('1.0', 'end-1c'), '验证期间编辑的内容')
                app.events.put(('saved_login_error', '登录已过期'))
                app.poll()
                self.assertFalse(app.logged_in)
                self.assertIn('disabled', app.send_button.state())
            finally:
                app.destroy()

    def test_saved_login_is_headless_and_never_clicks_qr(self):
        from playwright.sync_api import sync_playwright
        engine = MagicMock()
        browser = MagicMock()
        context = browser.new_context.return_value
        context.cookies.return_value = [{'name': 'sessionid', 'value': 'test-not-real'}]
        events = []
        with patch('playwright.sync_api.sync_playwright', return_value=engine), patch.object(gui, 'launch_browser', return_value=browser) as launch, patch.object(fire, 'STATE', MagicMock()), patch.object(gui, 'selectors', return_value={'search': 'input'}), patch.object(fire, 'wait_chat_ready'), patch.object(gui, 'read_json', return_value={'name': '测试账号'}), patch.object(gui, 'write_json'):
            gui.browser_login(True, lambda *event: events.append(event))
        self.assertTrue(launch.call_args.kwargs['headless'])
        context.new_page.return_value.get_by_text.assert_not_called()
        login = next(value for kind, value in events if kind == 'logged_in')
        self.assertEqual(login['name'], '测试账号')
        self.assertTrue(login['reuse'])
        stages = [value[0] for kind, value in events if kind == 'progress']
        self.assertEqual(stages, [1, 2, 3, 4, 7, 8, 9, 10])
        browser.close.assert_called_once()

    def test_expired_saved_login_never_opens_visible_browser(self):
        engine = MagicMock()
        browser = MagicMock()
        events = []
        with patch('playwright.sync_api.sync_playwright', return_value=engine), patch.object(gui, 'launch_browser', return_value=browser) as launch, patch.object(fire, 'STATE', MagicMock()), patch.object(gui, 'selectors', return_value={'search': 'input'}), patch.object(fire, 'wait_chat_ready', side_effect=RuntimeError('需要登录')):
            with self.assertRaisesRegex(RuntimeError, '扫码登录 / 切换账号'):
                gui.browser_login(True, lambda *event: events.append(event))
        self.assertTrue(launch.call_args.kwargs['headless'])
        self.assertFalse(any(kind == 'logged_in' for kind, value in events))
        browser.close.assert_called_once()

    def settings(self):
        return dict(friends=['好友'], text='你好 🔥', image='', interval='10', time='09:00')

    def test_validation(self):
        self.assertEqual(gui.validate(self.settings())['interval'], 10)
        for field, value in [('friends', []), ('friends', ['重名', '重名']), ('interval', 'nan'), ('interval', '-1'), ('time', '25:00'), ('image', 'missing.gif')]:
            data = self.settings()
            data[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                gui.validate(data)

    def test_empty_message(self):
        data = self.settings()
        data['text'] = '   '
        with self.assertRaises(ValueError):
            gui.validate(data)

    def test_schedule_exact_minute(self):
        self.assertEqual(gui.schedule_key(datetime(2026, 10, 14, 9, 0, 59), '09:00'), '2026-10-14 09:00')
        self.assertIsNone(gui.schedule_key(datetime(2026, 10, 14, 9, 1), '09:00'))
        self.assertIsNone(gui.schedule_key(datetime(2026, 10, 14, 8, 59), '09:00'))

    def test_ambiguous_recipient_refused(self):
        node = Mock()
        node.is_visible.return_value = True
        locator = Mock()
        locator.all.return_value = [node, node]
        with self.assertRaises(RuntimeError):
            fire.unique_visible(locator, '好友')
        node.click.assert_not_called()

    def test_loading_page_has_actionable_timeout(self):
        page = Mock()
        page.is_closed.return_value = False
        page.locator.return_value.all.return_value = []
        page.get_by_text.return_value.all.return_value = []
        with self.assertRaisesRegex(RuntimeError, '加载超时'):
            fire.wait_chat_ready(page, 'input', timeout=0)

    def test_login_required_during_loading(self):
        page = Mock()
        page.is_closed.return_value = False
        page.locator.return_value.all.return_value = []
        node = Mock()
        node.is_visible.return_value = True
        page.get_by_text.return_value.all.return_value = [node]
        with self.assertRaisesRegex(RuntimeError, '重新登录'):
            fire.wait_chat_ready(page, 'input', timeout=0)

    def test_retry_detected(self):
        node = Mock()
        node.is_visible.return_value = True
        page = Mock()
        page.get_by_text.return_value.all.return_value = [node]
        with self.assertRaises(RuntimeError):
            gui.reject_retry(page)

    def test_self_nickname_response(self):
        self.assertEqual(gui.self_nickname({'status_code': 0, 'user': {'nickname': '我的昵称'}}), '我的昵称')
        self.assertEqual(gui.self_nickname({'status_code': 1, 'user': {'nickname': '不应读取'}}), '')
        result = {}
        response = Mock(url='https://www.douyin.com/aweme/v1/web/user/profile/other/', ok=True)
        response.json.return_value = {'user': {'nickname': '好友昵称'}}
        gui.capture_self_response(response, result)
        self.assertEqual(result, {})
        response.url = 'https://www.douyin.com/aweme/v1/web/user/profile/self/'
        gui.capture_self_response(response, result)
        self.assertEqual(result['name'], '好友昵称')

    def test_friend_selection_limit_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gui, 'SETTINGS', Path(directory) / 'settings.json'), patch.object(gui.messagebox, 'showinfo'):
            app = gui.App()
            app.withdraw()
            try:
                app.dashboard('测试昵称')
                for index in range(20):
                    app.new_friend.set(f'好友{index}')
                    app.add_friend()
                self.assertEqual(len(app.friend_items), 20)
                app.new_friend.set('第21人')
                app.add_friend()
                self.assertEqual(len(app.friend_items), 20)
                app.friend_vars[0].set(False)
                data = app.collect()
                self.assertEqual(len(data['friends']), 19)
                self.assertNotIn('好友0', data['friends'])
                gui.write_json(gui.SETTINGS, data)
                app.dashboard('测试昵称')
                self.assertFalse(app.friend_vars[0].get())
                self.assertEqual(len(app.friend_items), 20)
                app.remove_friend(0)
                self.assertEqual(len(app.friend_items), 19)
            finally:
                app.destroy()

if __name__ == '__main__':
    unittest.main()
