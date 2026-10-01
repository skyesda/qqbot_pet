import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'petbot_framework/compat'))
from petpark.moonfest.wordcloud import keyword_counts, cloud_layout, wordcloud_html


class WordcloudTests(unittest.TestCase):
    def test_only_real_words_and_once_per_blessing(self):
        counts=keyword_counts(['祖国祖国，繁荣昌盛','祖国，国泰民安'])
        self.assertEqual(counts['祖国'],2)
        self.assertEqual(counts['繁荣昌盛'],1)
        self.assertNotIn('和平',counts)
    def test_empty_cloud_and_untrusted_text(self):
        self.assertIn('静候第一声祝福',wordcloud_html([],0))
        html=wordcloud_html(['<script>alert(1)</script>祝祖国国泰民安'],1)
        self.assertNotIn('<script>',html)
    def test_deterministic_positions_inside_canvas(self):
        counts=keyword_counts(['祝祖国繁荣昌盛，山河锦绣，国泰民安','愿华夏幸福安康，人民安居乐业，国庆快乐']*3)
        words=cloud_layout(counts)
        self.assertEqual(words,cloud_layout(counts))
        self.assertTrue(words)
        self.assertTrue(all(0<=w['x']<=615 and 0<=w['y']<=510 for w in words))


if __name__=='__main__': unittest.main()
