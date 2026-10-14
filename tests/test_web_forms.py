import copy
import json
import re
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from test_end_to_end import TEST_CONFIG
from tw_stock_backtest.web_forms import job_form,strategy_form,percent_ratio,ratio_percent,FormErrors
from tw_stock_backtest.strategies import from_settings,snapshot
from tw_stock_backtest.workbench import Workbench,create_app


class FormTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);config=root/'config.toml';config.write_text(TEST_CONFIG.replace('AAA','2330'))
        self.wb=Workbench(config,root/'reports',root/'jobs');self.addCleanup(self.wb.close)
        self.value=from_settings(self.wb.settings)
        self.client=create_app(self.wb).test_client()
        self.token=re.search('name="csrf" value="([^"]+)"',self.client.get('/strategies').text)[1]

    def form(self,value):
        f={k:str(value[k]) for k in ('id','name','description','ranking_mode')}
        f['conditions']=json.dumps(value['conditions']);f['csrf']=self.token
        for g in ('factors','screening'):f.update({g+'.'+k:str(v) for k,v in value[g].items()})
        return f

    def test_saved_settings_roundtrip_preserves_decimal_and_disabled_group(self):
        value=copy.deepcopy(self.value)
        value['factors']['momentum_weight']='0.123456789012345678901234567890123456789'
        value['conditions']=[{'type':'rsi','period':14,'minimum':'30.000000000000000000000000001','maximum':'70.125'}, {'type':'volume','period':20,'minimum':'1.000000000000000000000001'}]
        self.wb.library.save(value)
        response=self.client.post('/strategies',data={**self.form(value),'replace':'yes'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(snapshot(self.wb.library.load(value['id'])),snapshot(value))
        page=self.client.get('/strategies?id=baseline').text
        self.assertIn(value['factors']['momentum_weight'],page)
        self.assertIn('type="hidden" name="conditions"',page)
        self.assertNotIn('<label>ID ',page)

    def test_percent_conversion_exact_and_no_double_conversion(self):
        self.assertEqual(percent_ratio('0.1'),'0.001')
        precise='0.123456789012345678901234567890123456789'
        self.assertEqual(ratio_percent(percent_ratio(precise)),precise)
        f=job_form({'slippage_percent':'0.1','cash':'1000000.00001','stocks':'2330,2317，2454'})
        self.assertEqual(f['slippage'],'0.001');self.assertEqual(f['stocks'],'2330 2317 2454')
        self.assertEqual(job_form(f)['slippage'],'0.001')
        for value in ('100','-1','NaN','Infinity','bad'):
            with self.assertRaises(FormErrors):percent_ratio(value)
        with self.assertRaises(FormErrors):job_form({'slippage_percent':'0.1','slippage':'0.01'})

    def test_import_rejects_unknown_and_preserves_long_json_number(self):
        original=copy.deepcopy(self.value);self.wb.library.save(original)
        for change in ({'schema_version':2},{'unsupported':True},{'conditions':[{'type':'eval'}]}):
            response=self.client.post('/strategies',data={'csrf':self.token,'action':'import','replace':'yes','strategy_json':json.dumps({**original,**change})})
            self.assertEqual(response.status_code,400);self.assertIn('strategy_json',response.json['errors'])
            self.assertEqual(snapshot(original),snapshot(self.wb.library.load(original['id'])))
        raw=json.dumps(original).replace('"momentum_weight": "1"','"momentum_weight": 0.123456789012345678901234567890123456789')
        imported=strategy_form({'action':'import','strategy_json':raw})
        self.assertEqual(imported['factors']['momentum_weight'],'0.123456789012345678901234567890123456789')

    def test_ids_generated_for_create_copy_and_does_not_run_jobs(self):
        f=self.form(self.value);f['id']=''
        response=self.client.post('/strategies',data=f);self.assertEqual(response.status_code,302)
        first=self.wb.library.list()[0]
        response=self.client.post('/strategies',data={'csrf':self.token,'action':'copy','source':first['id'],'name':'副本'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(len({s['id'] for s in self.wb.library.list()}),2)
        self.assertEqual(self.wb.jobs(),[])

    def test_field_errors_do_not_save_or_execute(self):
        f=self.form(self.value);f['factors.long_window']='1'
        response=self.client.post('/strategies',data=f)
        self.assertEqual(response.status_code,400);self.assertIn('factors.long_window',response.json['errors'])
        self.assertEqual(self.wb.library.list(),[])
        for data,field in [({'kind':'backtest','start':'2026-09-29','end':'2026-09-01'},'end'),({'kind':'research','stocks':'2330;rm'},'stocks'),({'kind':'backtest','quantity':'1.5'},'quantity')]:
            response=self.client.post('/jobs',data={**data,'csrf':self.token})
            self.assertEqual(response.status_code,400);self.assertIn(field,response.json['errors'])
        self.assertEqual(self.wb.jobs(),[])

    def test_multiselect_and_period_form_pass_same_backend_values(self):
        periods=[{'id':'first','start':'2026-09-01','end':'2026-09-15','purpose':'validation','previously_seen':True}]
        data=MultiDict([('csrf',self.token),('kind','period_comparison'),('strategies','baseline'),('strategies','other'),('stocks','2330 2317'),('periods',json.dumps(periods))])
        with patch.object(self.wb,'submit',return_value='test') as submit:
            response=self.client.post('/jobs',data=data);self.assertEqual(response.status_code,302)
            passed=submit.call_args.args[0]
            self.assertEqual(passed['strategies'],'baseline other');self.assertEqual(json.loads(passed['periods']),periods)
        bad=copy.deepcopy(periods);bad[0]['unknown']=True
        with self.assertRaises(FormErrors):job_form({**data.to_dict(),'strategies':'baseline other','periods':json.dumps(bad)})
        # 比較表單移到 /new/backtest，策略多選由多選清單改為同名勾選框（送出的欄位相同）。
        for sid in ('baseline','other'):self.wb.library.save({**self.value,'id':sid,'name':sid})
        page=self.client.get('/new/backtest').text
        for sid in ('baseline','other'):self.assertIn(f'<input type="checkbox" name="strategies" value="{sid}">',page)
        self.assertIn('data-period-editor',page)
        self.assertNotIn('placeholder="baseline rsi_range',page)

    def test_percent_backtest_form_reaches_exported_settings(self):
        from test_strategy_library import records
        from tw_stock_backtest.data.database import save_records
        save_records(records(),db_path=self.wb.settings['storage']['database_path'])
        self.wb.library.save(self.value)
        response=self.client.post('/jobs',data={'csrf':self.token,'kind':'backtest','strategy_id':'baseline',
            'stocks':'2330','start':'2026-08-23','end':'2026-09-09','slippage_percent':'0.1',
            'cash':'100000.25','quantity':'3','max_positions':'1','sizing':'fixed_quantity'})
        self.assertEqual(response.status_code,302)
        self.wb.pool.submit(lambda:None).result(timeout=20)
        job=self.wb.jobs()[0];self.assertEqual(job['status'],'success')
        saved=json.loads(next((self.wb.state/job['run_id']/'reports').glob('*/settings.json')).read_text())
        self.assertEqual(Decimal(saved['settings']['backtest']['slippage_rate']),Decimal('0.001'))
        self.assertEqual(Decimal(saved['settings']['backtest']['initial_cash']),Decimal('100000.25'))
        self.assertEqual(saved['settings']['backtest']['quantity'],3)
        self.assertEqual(saved['strategy_snapshot'],snapshot(self.value))
