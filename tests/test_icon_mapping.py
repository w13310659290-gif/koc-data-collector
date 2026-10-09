import unittest

from koc_auto.browser import apply_icon_mapping


class IconMappingTests(unittest.TestCase):
    def data(self, candidates, raw=None):
        return {'raw':raw or {}, 'notes':[], 'iconCandidates':candidates}

    def test_only_explicit_shape_mapping_fills_values(self):
        data=self.data([{'signature':'heart','text':'568'},{'signature':'bubble','text':'25'},
                        {'signature':'star','text':'59'},{'signature':'arrow','text':'407'}])
        self.assertEqual(apply_icon_mapping(data,{})['raw'],{})
        self.assertEqual(apply_icon_mapping(data,{'star':'favorites','heart':'likes','arrow':'shares','bubble':'comments'})['raw'],
                         {'likes':'568','comments':'25','favorites':'59','shares':'407'})

    def test_changed_order_does_not_change_shape_mapping(self):
        data=self.data([{'signature':'star','text':'59'},{'signature':'heart','text':'568'}])
        self.assertEqual(apply_icon_mapping(data,{'star':'favorites','heart':'likes'})['raw'],{'favorites':'59','likes':'568'})

    def test_ambiguous_and_conflicting_counts_are_rejected(self):
        data=self.data([{'signature':'heart','text':'568'}],{'likes':'569'})
        self.assertNotIn('likes',apply_icon_mapping(data,{'heart':'likes'})['raw'])
        data=self.data([{'signature':'heart','text':'568'},{'signature':'heart','text':'568'}])
        self.assertNotIn('likes',apply_icon_mapping(data,{'heart':'likes'})['raw'])


if __name__ == '__main__':
    unittest.main()
