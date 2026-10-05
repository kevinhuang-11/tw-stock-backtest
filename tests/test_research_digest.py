import copy
import json
import unittest
from datetime import date
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tw_stock_backtest.notifications import build_message, preview_message, send_message
from tw_stock_backtest.research_digest import (
    build_digest, compare_history, differences, explain, load_digest, render_digest,
)

SETTINGS = {'short_window': 5, 'long_window': 20, 'momentum_window': 20,
            'volatility_window': 20, 'momentum_weight': '2', 'trend_weight': '1',
            'volatility_weight': '0', 'top_n': 3}


def bundle(day='2026-09-29', ids=('2330', '2317', '2454')):
    return {'summary': {'analysis_date': day, 'status': 'success', 'report_complete': True,
                        'run_id': day, 'ranking_population': list(ids), 'counts': {
                            'requested': len(ids), 'evaluated': len(ids), 'excluded': 0},
                        'evaluations': [], 'excluded': []},
            'settings': {'factors': dict(SETTINGS)}, 'mode': 'factors',
            'universe': {'stocks': [{'stock_id': sid, 'name': '', 'industry': ''} for sid in ids]},
            'path': day + '/summary.json', 'csv': '\ufeffrank,stock_id\r\n1,2330\r\n', 'csv_sha256': None,
            'rankings': [{'stock_id': sid, 'rank': i + 1, 'date': day, 'score': '75',
                          'factors': {'momentum': '-0.02', 'trend': '0.01', 'volatility': '0.03'},
                          'factor_scores': {'momentum': '100', 'trend': '25', 'volatility': '0'}}
                         for i, sid in enumerate(ids)]}


class DigestTests(unittest.TestCase):
    def test_weighted_contributions_zero_weight_and_negative_momentum(self):
        b = bundle()
        lines, hints, contributions = explain(b['rankings'][0], SETTINGS)
        self.assertEqual(Decimal(contributions['volatility']), 0)
        self.assertAlmostEqual(float(contributions['momentum']), 200 / 3)
        self.assertIn('加權貢獻較大：動能。', lines)
        self.assertFalse(any('較大：低波動' in x for x in lines))
        self.assertTrue(any('為負' in x for x in hints))
        self.assertTrue(any('起伏相對較大' in x for x in hints))
        self.assertTrue(any('較前' in x for x in lines))

    def test_tied_close_and_missing_inputs(self):
        row = bundle()['rankings'][0]
        row['factor_scores'] = dict.fromkeys(('momentum', 'trend', 'volatility'), '50')
        settings = {**SETTINGS, 'momentum_weight': '1', 'volatility_weight': '1'}
        lines, _, _ = explain(row, settings)
        self.assertTrue(any('貢獻接近' in x for x in lines))
        row['factor_scores']['trend'] = '51'
        self.assertTrue(any('貢獻接近' in x for x in explain(row, settings)[0]))
        row['factor_scores']['trend'] = None
        self.assertTrue(any('不足' in x for x in explain(row, settings)[0]))
        self.assertEqual(explain(row, settings)[2], {})

    def test_no_history_and_same_day(self):
        b = bundle()
        for histories in ([], [bundle()], [bundle('2026-09-30')]):
            result = compare_history(b, histories, 2)
            self.assertFalse(result['available'])
            self.assertEqual(result['message'], '尚無可比較的前期結果')

    def test_latest_comparable_and_true_top_entries(self):
        current = bundle(ids=('2454', '2330', '2317'))
        old = bundle('2026-09-24')
        incompatible = bundle('2026-09-28')
        incompatible['settings']['factors']['trend_weight'] = '2'
        result = compare_history(current, [old, incompatible, bundle()], 2)
        self.assertEqual(result['analysis_date'], '2026-09-24')
        self.assertEqual(result['entered'], ['2454'])
        self.assertEqual(result['exited'], ['2317'])
        self.assertEqual(result['changes']['2454'], 2)

    def test_population_change_is_not_rank_decline(self):
        current, previous = bundle(), bundle('2026-09-24')
        previous['summary']['ranking_population'].remove('2330')
        previous['rankings'] = previous['rankings'][1:]
        result = compare_history(current, [previous], 2)
        self.assertFalse(result['available'])
        self.assertEqual(result['previously_unranked'], ['2330'])
        self.assertEqual(result['entered'], [])
        self.assertEqual(result['changes'], {})
        previous['universe']['stocks'].pop()
        self.assertTrue(any('指定股票池' in x for x in differences(current, previous)))

    def test_settings_mode_and_presentation_top(self):
        a, b = bundle(), bundle('2026-09-24')
        b['settings']['factors']['top_n'] = 20
        self.assertEqual(differences(a, b), [])
        b['mode'] = 'rules'
        self.assertTrue(differences(a, b))
        b['mode'] = 'factors'
        b['settings']['factors']['momentum_window'] = 21
        self.assertTrue(differences(a, b))

    def test_metadata_concentration_and_old_prices(self):
        b = bundle()
        d = build_digest(b, today=date(2026, 10, 5))
        self.assertIsNone(d['candidates'][0]['close'])
        self.assertIsNone(d['candidates'][0]['name'])
        self.assertTrue(d['title'].startswith('【歷史行情】'))
        self.assertFalse(any('產業集中' in x for x in d['hints']))
        for stock in b['universe']['stocks'][:2]:
            stock['industry'] = '半導體'
        b['summary']['evaluations'] = [{'stock_id': '2330', 'signal': {'close': '123.45', 'date': '2026-09-29'}}]
        d = build_digest(b)
        self.assertEqual(d['candidates'][0]['close'], '123.45')
        self.assertTrue(any('2/3' in x for x in d['hints']))
        with self.assertRaises(ValueError):
            build_digest(b, top=51)

    def test_html_text_csv_and_preview_match(self):
        b = bundle()
        b['universe']['stocks'][0]['name'] = '<script>alert("x")</script>'
        d = build_digest(b)
        message = build_message(d)
        html = message.get_body(preferencelist=('html',)).get_content()
        text = message.get_body(preferencelist=('plain',)).get_content()
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<script>', text)
        attachment = list(message.iter_attachments())[0]
        self.assertEqual(attachment.get_payload(decode=True), b['csv'].encode())
        with TemporaryDirectory() as temp, patch('smtplib.SMTP_SSL') as smtp:
            path = Path(temp) / 'report.eml'
            preview_message(d, path)
            saved = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            self.assertEqual(saved.get_body(preferencelist=('plain',)).get_content(), text)
            derived = Path(str(path) + '.preview')
            self.assertEqual((derived / 'report.html').read_text(), render_digest(d)[1])
            self.assertEqual(load_digest(derived / 'research-summary.json'), d)
            self.assertEqual(smtp.call_count, 0)
            with self.assertRaises(FileExistsError):
                preview_message(d, path)

    def test_mock_send_uses_same_rich_content_once(self):
        d = build_digest(bundle())
        with patch.dict('os.environ', {'GMAIL_ADDRESS': 'test@gmail.com', 'GMAIL_APP_PASSWORD': 'fake'}), \
                patch('smtplib.SMTP_SSL') as smtp:
            send_message(d)
            sender = smtp.return_value.__enter__.return_value
            sender.send_message.assert_called_once()
            message = sender.send_message.call_args.args[0]
            self.assertEqual(message['To'], message['From'])
            self.assertEqual(message.get_body(preferencelist=('plain',)).get_content(),
                             build_message(d).get_body(preferencelist=('plain',)).get_content())

    def test_load_job_and_legacy_missing_information(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / 'reports/run'
            report.mkdir(parents=True)
            b = bundle()
            for name, data in [('summary.json', b['summary']), ('rankings.json', b['rankings'])]:
                (report / name).write_text(json.dumps(data))
            job = root / 'job.json'
            job.write_text('{"kind":"research","status":"success"}')
            d = load_digest(job)
            self.assertIsNone(d['ranking_mode'])
            self.assertIsNone(d['specified_pool'])
            self.assertIsNone(d['ranking_csv'])
            self.assertTrue(any('不足' in x for x in d['candidates'][0]['explanations']))
            self.assertIsNone(d['candidates'][0]['close'])

    def test_incomplete_preview_remains_marked_on_failure(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / 'test.eml'
            with patch('tw_stock_backtest.notifications.os.link', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    preview_message(build_digest(bundle()), output)
            self.assertFalse(output.exists())
            self.assertTrue(Path(str(output) + '.preview.incomplete').exists())
            self.assertFalse(Path(str(output) + '.preview').exists())


if __name__ == '__main__':
    unittest.main()
