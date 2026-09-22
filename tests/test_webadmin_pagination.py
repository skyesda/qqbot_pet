"""Admin pagination must preserve search, exports, and fresh edit snapshots."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent/'petbot_framework/compat'))
from aiohttp import web
from petpark.admin_paging import paginate
from petpark.store import PetStore
from petpark.webadmin import WebAdmin


class Request:
    def __init__(self, body=None, authenticated=True):
        self.body = body or {}
        self.query = self.body
        self.cookies = {'pp_session':'test-session'} if authenticated else {}

    async def json(self):
        return self.body


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name)/'store.json')
        self.admin = WebAdmin(self.store, '127.0.0.1', 0, 'test', 'unused')
        self.admin._tokens.add('test-session')
        self.store._data['players'] = {
            f'group\x1f{i}': {'group':'group','qq':str(i), 'pets':[{'nickname':f'灵宠{i}', 'level':i}],
                             'coin':i, 'bag':{'仅末页出现的道具' if i==26 else '灵石':1}}
            for i in range(27)
        }
        self.store._data['cards'] = {f'CARD{i}':{'used':i%2==0,'created_at':i} for i in range(27)}

    async def call(self, method, body=None):
        response = await getattr(self.admin,method)(Request(body))
        return json.loads(response.text)

    async def test_pages_are_ten_and_complete(self):
        seen=[]
        for page, expected in [(1,10),(2,10),(3,7)]:
            r=await self.call('_api_list',{'table':'players','page':page,'size':10000})
            self.assertEqual((r['size'],r['total'],len(r['data'])),(10,27,expected))
            seen.extend(r['data'])
        self.assertEqual(seen,list(self.store._data['players']))

    async def test_search_before_paging_and_empty_state(self):
        r=await self.call('_api_list',{'table':'players','q':'仅末页出现的道具','page':99})
        self.assertEqual((r['total'],r['page']), (1,1))
        self.assertEqual(list(r['data']),['group\x1f26'])
        empty=await self.call('_api_list',{'table':'players','q':'not-found','page':5})
        self.assertEqual((empty['page'],empty['total'],empty['data']), (1,0,{}))

    async def test_page_clamps_after_last_record_deleted(self):
        self.store._data['groups']={str(i):{} for i in range(11)}
        del self.store._data['groups']['10']
        r=await self.call('_api_list',{'table':'groups','page':2})
        self.assertEqual((r['page'],len(r['data'])),(1,10))

    async def test_detail_is_current_and_does_not_expand_page(self):
        self.store._data['players']['group\x1f26']['coin']=900
        r=await self.call('_api_list',{'table':'players','key':'group\x1f26'})
        self.assertEqual(list(r['data']),['group\x1f26'])
        self.assertEqual(r['data']['group\x1f26']['coin'],900)
        missing=await self.call('_api_list',{'table':'players','key':'missing'})
        self.assertEqual(missing['data'],{})

    async def test_export_and_statistics_span_all_pages(self):
        page=await self.call('_api_list',{'table':'cards','page':2,'q':'CARD2'})
        self.assertEqual(page['stats'],{'total':27,'used':14})
        result=await self.call('_api_list',{'table':'cards','export':'unused','q':'CARD2'})
        self.assertEqual(len(result['data']),13)
        self.assertTrue(all(not v['used'] for v in result['data'].values()))

    async def test_review_and_feedback_filters_precede_paging(self):
        self.store._data['custom_reviews']={str(i):{'id':str(i),'kind':'mount','status':'pending','created_at':i} for i in range(27)}
        reviews=await self.call('_api_custom_reviews',{'page':2,'status':'pending','kind':'mount'})
        self.assertEqual((reviews['total'],len(reviews['data'])),(27,10))
        self.assertEqual(reviews['data'][0]['id'],'16')
        self.store._data['feedbacks']={str(i):{'id':str(i),'content':f'问题{i}','status':'pending','created_at':i} for i in range(27)}
        feedback=await self.call('_api_feedbacks',{'page':3,'status':'pending'})
        self.assertEqual((feedback['total'],len(feedback['data'])),(27,7))

    async def test_audit_log_and_flags_have_ten_items(self):
        self.store._data['audit_log']=[{'ts':i,'action':'buy','group':'g','pid':'p','detail':'买道具'} for i in range(27)]
        result=await self.call('_api_audit_query',{'page':2,'size':200,'group':'g'})
        self.assertEqual((result['size'],result['total'],len(result['items'])),(10,27,10))
        self.assertEqual(result['items'][0]['ts'],16)
        self.store._data['audit_flags']={str(i):{'status':'open','ts':i} for i in range(27)}
        flags=await self.call('_api_audit_flags',{'page':3,'status':'open'})
        self.assertEqual((flags['total'],len(flags['items']),flags['open_count']),(27,7,27))

    async def test_accounts_and_custom_assets_are_paged(self):
        self.store._data['accounts']={str(i):{'qq':str(i),'bound_pets':[]} for i in range(27)}
        accounts=await self.call('_api_portal_accounts',{'page':2})
        self.assertEqual((accounts['total'],len(accounts['data'])),(27,10))
        for p in self.store._data['players'].values():
            p['pet']={'custom':True,'nickname':'试用灵宠'}
            p['mounts']={'试用坐骑':{'custom':True,'power':123}}
        for method in ['_api_custom_pets','_api_custom_mounts']:
            r=await self.call(method,{'page':3})
            self.assertEqual((r['total'],len(r['data'])),(27,7))

    async def test_reading_never_changes_records_and_requires_auth(self):
        before=copy.deepcopy(self.store._data)
        await self.call('_api_list',{'table':'players','page':2})
        self.assertEqual(before,self.store._data)
        with self.assertRaises(web.HTTPFound):
            await self.admin._api_list(Request({'table':'players'},authenticated=False))

    def test_malformed_page_inputs_and_unicode(self):
        for page in ['oops',None,-30,0,10**30]:
            r=paginate([{'name':'九尾狐'}]*12,{'page':page})
            self.assertIn(r['page'],[1,2]);self.assertLessEqual(len(r['data']),10)
        self.assertEqual(paginate([{'name':'九尾狐'}],{'q':' 九尾 '})['total'],1)


if __name__=='__main__':
    unittest.main()
