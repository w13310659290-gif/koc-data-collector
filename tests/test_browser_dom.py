import os
import shutil
import unittest

from koc_auto.browser import EXTRACT


class BrowserDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('浏览器夹具测试需要 Playwright')
        cls.engine = sync_playwright().start()
        try:
            if os.name == 'nt':
                cls.browser = cls.engine.chromium.launch(channel='msedge', headless=True)
            else:
                executable = shutil.which('chromium')
                cls.browser = cls.engine.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
        except Exception:
            cls.engine.stop()
            raise unittest.SkipTest('未安装测试浏览器')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.engine.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_douyin_player_only_ignores_recommendations_and_comment_likes(self):
        self.page.set_content('''<div data-e2e="video-detail">
          <a data-e2e="video-author-name" href="https://www.douyin.com/user/author123">作者</a>
          <div data-e2e="video-player-digg">1109</div><div data-e2e="video-player-comment">84</div>
          <div data-e2e="video-player-collect">14</div><div data-e2e="video-player-share">71</div>
          <section class="comments"><span class="like-count">9000</span></section></div>
          <section class="recommendations"><span>19.9万</span></section>''')
        data = self.page.evaluate(EXTRACT, 'douyin')
        self.assertEqual(data['raw'], {'likes': '1109', 'comments': '84', 'favorites': '14', 'shares': '71'})
        self.assertTrue(data['author'].endswith('/user/author123'))

    def test_xhs_missing_share_is_not_zero_and_feed_ignored(self):
        self.page.set_content('''<div class="note-detail-mask"><div class="note-container">
          <div class="author-container"><a href="https://www.xiaohongshu.com/user/profile/0123456789abcdef01234567">作者</a></div>
          <div class="interaction-container"><div class="like-wrapper"><span class="count">130</span></div>
          <div class="collect-wrapper"><span class="count">128</span></div>
          <div class="chat-wrapper"><span class="count">6</span></div><div class="share-wrapper">分享</div></div>
          <div class="comment"><span class="count">500</span></div></div></div>
          <div class="interaction-container"><div class="like-wrapper"><span class="count">9999</span></div></div>''')
        data = self.page.evaluate(EXTRACT, 'xiaohongshu')
        self.assertEqual(data['raw'], {'likes': '130', 'comments': '6', 'favorites': '128'})

    def test_comment_like_controls_do_not_conflict_with_note_bar(self):
        self.page.set_content('''<div class="note-detail-mask"><div class="note-container">
          <a class="author" href="https://www.xiaohongshu.com/user/profile/0123456789abcdef01234567?source=note">作者</a>
          <div class="interaction-container"><div class="like-wrapper"><span class="count">500</span></div><div class="chat-wrapper"><span class="count">2</span></div></div>
          <div class="interaction-container engage-bar"><div class="like-wrapper"><span class="count"><span class="count">130</span></span></div>
          <div class="collect-wrapper"><span class="count">128</span></div><div class="chat-wrapper"><span class="count">6</span></div></div>
        </div></div>''')
        data = self.page.evaluate(EXTRACT, 'xiaohongshu')
        self.assertEqual(data['raw'], {'likes': '130', 'comments': '6', 'favorites': '128'})
        self.assertTrue(data['author'].endswith('0123456789abcdef01234567'))
        self.assertNotIn('?', data['author'])

    def test_separate_work_bars_remain_ambiguous(self):
        bar = '<div class="engage-bar"><div class="like-wrapper"><span class="count">130</span></div><div class="collect-wrapper"><span class="count">128</span></div><div class="chat-wrapper"><span class="count">6</span></div></div>'
        self.page.set_content('<div class="note-detail-mask">' + bar + bar + '</div>')
        self.assertEqual(self.page.evaluate(EXTRACT, 'xiaohongshu')['raw'], {})

    def test_ambiguous_controls_fail_closed_even_same_values(self):
        self.page.set_content('<div data-e2e="video-player-digg">12</div><div data-e2e="video-player-digg">12</div>')
        data = self.page.evaluate(EXTRACT, 'douyin')
        self.assertNotIn('likes', data['raw'])
        self.assertTrue(data['notes'])

    def test_hidden_controls_and_unknown_layout_are_not_read(self):
        self.page.set_content('<div data-e2e="video-player-digg" style="display:none">99</div><div class="recommendation">点赞 123</div>')
        self.assertEqual(self.page.evaluate(EXTRACT, 'douyin')['raw'], {})

    def test_alternate_douyin_detail_controls_and_semantic_icons(self):
        self.page.set_content('''<div data-e2e="video-detail"><div data-e2e="detail-video-info">
          <div><svg><use href="#icon-like"></use></svg><span>568</span></div>
          <div><svg><use href="#icon-collect"></use></svg><span>59</span></div>
          <div data-e2e="video-share-icon-container">407</div>
        </div><button role="tab">评论(25)</button>
        <div data-e2e="comment-list"><div data-e2e="comment-item"><div><svg aria-label="点赞"></svg>999</div></div></div>
        <div data-e2e="related-video"><div data-e2e="video-share-icon-container">9999</div></div></div>''')
        data = self.page.evaluate(EXTRACT, 'douyin')
        self.assertEqual(data['raw'], {'likes':'568','comments':'25','favorites':'59','shares':'407'})

    def test_unknown_anonymous_icons_are_not_mapped_by_order(self):
        self.page.set_content('''<div data-e2e="video-detail"><div data-e2e="detail-video-info">
          <div><svg><use href="#anonymous"></use></svg>568</div>
          <div><svg></svg>59</div><div data-e2e="video-share-icon-container">407</div>
        </div></div>''')
        self.assertEqual(self.page.evaluate(EXTRACT, 'douyin')['raw'], {'shares':'407'})

    def test_observed_feed_comment_icon_is_scoped_to_current_note(self):
        self.page.set_content('''<main data-e2e="note-detail"><div data-e2e="video-player-digg">1109</div>
        <div data-e2e="feed-comment-icon">84</div><div data-e2e="video-player-collect">14</div>
        <div data-e2e="video-player-share">71</div><div data-e2e="related-video"><div data-e2e="feed-comment-icon">9999</div></div></main>''')
        self.assertEqual(self.page.evaluate(EXTRACT,'douyin')['raw'],{'likes':'1109','comments':'84','favorites':'14','shares':'71'})

    def test_shape_candidates_keep_geometry_without_assigning_meanings(self):
        self.page.set_content('''<div data-e2e="video-detail"><div data-e2e="detail-video-info"><div><div><div>
          <svg viewBox="0 0 24 24"><path d="M 0 0 L 1 1 Z"></path></svg></div></div><span>568</span></div></div></div>''')
        data=self.page.evaluate(EXTRACT,'douyin')
        self.assertEqual(data['raw'],{})
        self.assertEqual(len(data['iconCandidates']),1)
        self.assertEqual(data['iconCandidates'][0]['text'],'568')
        self.assertIn('M 0 0 L 1 1 Z',data['iconCandidates'][0]['signature'])


if __name__ == '__main__':
    unittest.main()
