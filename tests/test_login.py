import tempfile
import threading
import unittest
from unittest.mock import Mock

from koc_auto.browser import BrowserCollector


class LoginTests(unittest.TestCase):
    def collector(self, confirm):
        instance = BrowserCollector('profile', 'reports', Mock(return_value=confirm), Mock(), threading.Event())
        instance.context = Mock()
        return instance

    def test_login_is_prepared_once_in_collection_context(self):
        instance = self.collector(True)
        self.assertTrue(instance.prepare_xhs_login())
        self.assertTrue(instance.prepare_xhs_login())
        instance.context.new_page.assert_called_once()
        instance.context.new_page.return_value.goto.assert_called_once_with(
            'https://www.xiaohongshu.com/explore', wait_until='domcontentloaded', timeout=30000)
        instance.context.new_page.return_value.close.assert_called_once()
        instance.prompt.assert_called_once()

    def test_cancel_skips_all_xhs_works_without_navigation(self):
        instance = self.collector(False)
        first = instance.resolve('https://www.xiaohongshu.com/explore/0123456789abcdef01234567', 'xiaohongshu')
        second = instance.resolve('https://xhslink.cn/example', 'xiaohongshu')
        self.assertEqual(first.outcome, '需要登录')
        self.assertEqual(second.outcome, '需要登录')
        self.assertTrue(all(v is None for v in first.values.values()))
        instance.context.new_page.assert_called_once()

    def test_load_failure_is_not_reported_as_logged_in(self):
        instance = self.collector(True)
        instance.context.new_page.return_value.goto.side_effect = RuntimeError('network')
        self.assertFalse(instance.prepare_xhs_login())
        instance.prompt.assert_not_called()
        self.assertNotIn('xiaohongshu', instance.login_prepared)


if __name__ == '__main__':
    unittest.main()
