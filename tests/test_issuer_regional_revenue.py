import unittest
from types import SimpleNamespace

from research.data_pipeline.extract_issuer_regional_revenue import contiguous, frame_for


class RegionalRevenuePhysicalFramesTest(unittest.TestCase):
    def test_header_only_merged_period_cells_keep_real_column_edges(self):
        values=[["", "本报告期", None, "上年同期", None], ["项目", None,None,None,None],
                [None,"金额","占营业收入比重","金额","占营业收入比重"]]
        rectangles=[[(0,0,100,40),(100,0,300,20),None,(300,0,500,20),None],
                    [None,None,None,None,None],
                    [None,(100,20,200,40),(200,20,300,40),(300,20,400,40),(400,20,500,40)]]
        table=SimpleNamespace(extract=lambda:values,rows=[SimpleNamespace(cells=r) for r in rectangles],col_count=5,bbox=(0,0,500,40))
        result=frame_for(table,1,800)
        self.assertEqual(result['column_edges'],[0,100,200,300,400,500])
        self.assertIsNone(result['rows'][0][2])

    def test_unproved_column_partition_is_not_equally_split(self):
        table=SimpleNamespace(extract=lambda:[["2019年上半年",None,None]],rows=[SimpleNamespace(cells=[(0,0,300,20),None,None])],col_count=3,bbox=(0,0,300,20))
        self.assertEqual(frame_for(table,1,800)['column_edges'],[])

    def test_nonadjacent_page_or_table_is_not_a_continuation(self):
        previous={'pdf_page':1,'bbox':[0,600,300,760],'page_height':800,'column_edges':[0,100,200,300]}
        current={'pdf_page':2,'bbox':[0,60,300,300],'page_height':800,'column_edges':[0,100,200,300]}
        self.assertTrue(contiguous(previous,current))
        current['pdf_page']=3
        self.assertFalse(contiguous(previous,current))
        current['pdf_page']=1;current['bbox'][1]=790
        self.assertFalse(contiguous(previous,current))


if __name__ == '__main__':
    unittest.main()
