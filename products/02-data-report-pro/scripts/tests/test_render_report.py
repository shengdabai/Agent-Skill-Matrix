"""Tests for scripts/render_report.py and for the example report embedded in the template.

Run from the skill root:  python3 -I -m unittest discover -s scripts/tests -v
"""
import csv
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # do not leave scripts/__pycache__ behind

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
CSV_PATH = ROOT / 'sample' / 'campaign-data-sample.csv'
TEMPLATE_PATH = ROOT / 'references' / 'report-template.html'


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_report = load_script('render_report')
START, END = render_report.START, render_report.END

STUB_HEAD = '<!doctype html><body><p>before</p>\n'
STUB_TAIL = '\n<script>const DATA = JSON.parse(document.getElementById("report-data").textContent);</script><p>after</p></body>'
STUB = STUB_HEAD + START + '\nPLACEHOLDER\n' + END + STUB_TAIL

NASTY = 'x</script><img src=x onerror=alert(1)>a<b>c&amp;d\u2028e\u2029f'


def valid_data():
    return {
        'meta': {
            'kicker': '投放数据复盘', 'headline': '结论式标题', 'summary': '一句话摘要', 'source': 'x.csv',
            'rows': '2 行 · 3 列', 'period': '2026-01 ~ 2026-02', 'date': '2026-02-01',
        },
        'kpis': [{'label': '总消耗', 'value': '¥1,000', 'delta': '持平', 'dir': 'flat'}],
        'findings': [
            {
                'rank': '发现 1', 'title': '标题', 'body': '正文', 'sowhat': '行动',
                'chart': {'type': 'bar', 'title': '图', 'labels': ['a', 'b', 'c'],
                          'datasets': [{'label': 'ROI', 'data': [1.0, 2.5, 3]}]},
            },
            {
                'rank': '发现 2', 'title': '标题', 'body': '正文', 'sowhat': '行动',
                'chart': {'type': 'scatter', 'title': '图',
                          'datasets': [{'label': '点', 'data': [{'x': 1, 'y': 2.5}, {'x': 3, 'y': 4}]}]},
            },
        ],
        'watch': ['注意'],
        'recs': [{'t': '做这件事', 'note': '因为'}],
        'table': {'head': ['a', 'b'], 'rows': [['1', '2'], ['3', '4', 'neg']]},
    }


def data_block(html):
    """The text between the report-data tags."""
    return html.split(START, 1)[1].split(END, 1)[0]


class TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write_json(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding='utf-8')
        return path

    def run_cli(self, data_path, output_path):
        argv = ['render_report.py', '--data', str(data_path), '--output', str(output_path)]
        with mock.patch.object(sys, 'argv', argv):
            render_report.main()


class RenderTest(TempDirCase):
    def test_valid_data_renders_into_stub(self):
        data = valid_data()
        html = render_report.render(data, STUB)
        self.assertTrue(html.startswith(STUB_HEAD + START))
        self.assertTrue(html.endswith(END + STUB_TAIL))
        self.assertEqual(html.count(START), 1)
        self.assertEqual(json.loads(data_block(html)), data)
        self.assertNotIn('PLACEHOLDER', html)

    def test_valid_data_renders_into_real_template(self):
        template = TEMPLATE_PATH.read_text(encoding='utf-8')
        data = valid_data()
        html = render_report.render(data, template)
        self.assertEqual(html.count(START), 1)
        self.assertEqual(json.loads(data_block(html)), data)
        # Everything outside the data block is left alone.
        self.assertEqual(html.split(START, 1)[0], template.split(START, 1)[0])
        self.assertEqual(html.split(END)[-1], template.split(END)[-1])

    def test_cli_writes_a_report(self):
        data_path = self.write_json('data.json', json.dumps(valid_data(), ensure_ascii=False))
        output = self.tmp / 'report.html'
        self.run_cli(data_path, output)
        html = output.read_text(encoding='utf-8')
        self.assertEqual(json.loads(data_block(html)), valid_data())

    def test_cli_rejects_nan_and_infinity(self):
        for token, value in (('NaN', math.nan), ('Infinity', math.inf), ('-Infinity', -math.inf)):
            with self.subTest(token=token):
                data = valid_data()
                data['kpis'][0]['value'] = value
                text = json.dumps(data)  # Python writes the bare NaN / Infinity token
                self.assertIn(token, text)
                data_path = self.write_json('bad.json', text)
                output = self.tmp / ('out-' + token + '.html')
                with self.assertRaisesRegex(ValueError, 'Non-finite JSON value: ' + token):
                    self.run_cli(data_path, output)
                self.assertFalse(output.exists())

    def test_validate_rejects_non_finite_numbers(self):
        def in_kpi(data, value): data['kpis'][0]['value'] = value
        def in_table(data, value): data['table']['rows'][0][1] = value
        def in_bar(data, value): data['findings'][0]['chart']['datasets'][0]['data'][1] = value
        def in_scatter_x(data, value): data['findings'][1]['chart']['datasets'][0]['data'][0]['x'] = value
        def in_scatter_y(data, value): data['findings'][1]['chart']['datasets'][0]['data'][0]['y'] = value
        for place in (in_kpi, in_table, in_bar, in_scatter_x, in_scatter_y):
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(place=place.__name__, value=value):
                    data = valid_data()
                    place(data, value)
                    with self.assertRaises(ValueError):
                        render_report.validate(data)
                    with self.assertRaises(ValueError):
                        render_report.render(data, STUB)

    def test_validate_rejects_bool_as_number(self):
        data = valid_data()
        data['findings'][0]['chart']['datasets'][0]['data'][0] = True
        with self.assertRaisesRegex(ValueError, 'expected finite number'):
            render_report.validate(data)

    def test_rejects_numbers_that_lose_integer_precision_in_javascript(self):
        for value in (2**53 + 1, -(2**53 + 1), float(2**53), 10**400):
            with self.subTest(value=value):
                data = valid_data()
                data['table']['rows'][0][0] = value
                with self.assertRaisesRegex(ValueError, r'table.rows\[0\]\[0\].*safe integer range'):
                    render_report.render(data, STUB)

    def test_safe_integer_boundaries_and_large_text_values_round_trip(self):
        for value in (2**53 - 1, -(2**53 - 1), str(10**400)):
            with self.subTest(value=value):
                data = valid_data()
                data['table']['rows'][0][0] = value
                html = render_report.render(data, STUB)
                self.assertEqual(json.loads(data_block(html)), data)

    def test_html_and_line_separator_characters_are_encoded(self):
        data = valid_data()
        data['meta']['headline'] = NASTY
        data['kpis'][0]['label'] = NASTY
        data['findings'][0]['title'] = NASTY
        data['findings'][0]['chart']['title'] = NASTY
        data['findings'][0]['chart']['labels'][0] = NASTY
        data['findings'][0]['chart']['datasets'][0]['label'] = NASTY
        data['watch'][0] = NASTY
        data['recs'][0]['t'] = NASTY
        data['table']['head'][0] = NASTY
        data['table']['rows'][0][0] = NASTY
        html = render_report.render(data, STUB)
        block = data_block(html)
        for char in ('<', '>', '&', '\u2028', '\u2029'):
            self.assertNotIn(char, block, repr(char))
        for escape in ('\\u003c', '\\u003e', '\\u0026', '\\u2028', '\\u2029'):
            self.assertIn(escape, block)
        self.assertEqual(json.loads(block), data)  # the browser decodes it back to the original text
        # The injected </script> and <img> never become real tags: only the stub's own two closing tags remain.
        self.assertEqual(html.count('</script>'), STUB.count('</script>'))
        self.assertNotIn('<img', html)

    def test_existing_output_is_never_overwritten(self):
        data_path = self.write_json('data.json', json.dumps(valid_data(), ensure_ascii=False))
        output = self.tmp / 'report.html'
        output.write_text('my earlier deliverable', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.run_cli(data_path, output)
        self.assertEqual(output.read_text(encoding='utf-8'), 'my earlier deliverable')

    def test_cli_encoding_failure_leaves_no_empty_deliverable(self):
        data = valid_data()
        data['meta']['headline'] = 'invalid Unicode: \ud800'
        data_path = self.write_json('bad-unicode.json', json.dumps(data, ensure_ascii=True))
        output = self.tmp / 'report.html'
        with self.assertRaises(UnicodeEncodeError):
            self.run_cli(data_path, output)
        self.assertFalse(output.exists())

    def test_template_without_a_data_block_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one report-data block'):
            render_report.render(valid_data(), '<html><body>no data block here</body></html>')

    def test_template_with_two_data_blocks_is_rejected(self):
        template = STUB + '\n' + STUB
        self.assertEqual(template.count(START), 2)
        with self.assertRaisesRegex(ValueError, 'exactly one report-data block'):
            render_report.render(valid_data(), template)

    def test_the_real_template_has_exactly_one_data_block(self):
        self.assertEqual(TEMPLATE_PATH.read_text(encoding='utf-8').count(START), 1)


class ValidateTest(unittest.TestCase):
    def assert_invalid(self, data, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            render_report.validate(data)

    def test_valid_data_passes(self):
        render_report.validate(valid_data())

    def test_optional_axis_titles_are_preserved(self):
        data = valid_data()
        data['findings'][1]['chart'].update(xTitle='温度(°C)', yTitle='用电量(kWh)')
        html = render_report.render(data, STUB)
        self.assertEqual(json.loads(data_block(html)), data)

    def test_axis_titles_must_be_strings(self):
        for key in ('xTitle', 'yTitle'):
            for value in (None, 1, True, [], {}):
                with self.subTest(key=key, value=value):
                    data = valid_data()
                    data['findings'][1]['chart'][key] = value
                    self.assert_invalid(data, rf'findings\[1\]\.chart\.{key}: expected string')

    def test_missing_meta(self):
        data = valid_data()
        del data['meta']
        self.assert_invalid(data, r'^meta: expected object')

    def test_invalid_kpi_direction(self):
        data = valid_data()
        data['kpis'][0]['dir'] = 'sideways'
        self.assert_invalid(data, r'kpis\[0\]\.dir: expected up/down/flat')

    def test_scatter_point_missing_x(self):
        data = valid_data()
        del data['findings'][1]['chart']['datasets'][0]['data'][0]['x']
        self.assert_invalid(data, r'findings\[1\]\.chart\.datasets\[0\]\.data\[0\]\.x: expected finite number')

    def test_scatter_point_missing_y(self):
        data = valid_data()
        del data['findings'][1]['chart']['datasets'][0]['data'][1]['y']
        self.assert_invalid(data, r'findings\[1\]\.chart\.datasets\[0\]\.data\[1\]\.y: expected finite number')

    def test_dataset_length_differs_from_labels(self):
        data = valid_data()
        data['findings'][0]['chart']['datasets'][0]['data'] = [1.0, 2.5]  # 3 labels, 2 values
        self.assert_invalid(data, r'findings\[0\]\.chart\.datasets\[0\]\.data: expected same length as labels')

    def test_empty_chart_data_is_rejected(self):
        for chart_type in ('bar', 'line', 'doughnut', 'scatter'):
            with self.subTest(chart_type=chart_type):
                data = valid_data()
                chart = data['findings'][0]['chart']
                chart.update(type=chart_type, labels=[])
                chart['datasets'][0]['data'] = []
                self.assert_invalid(data, r'findings\[0\]\.chart\..*: expected nonempty array')

    def test_doughnut_rejects_negative_values_and_zero_totals(self):
        for values in ([-1, 2, 3], [0, 0, 0]):
            with self.subTest(values=values):
                data = valid_data()
                chart = data['findings'][0]['chart']
                chart['type'] = 'doughnut'
                chart['datasets'][0]['data'] = values
                self.assert_invalid(data, r'findings\[0\]\.chart\.datasets\[0\].*: expected .*doughnut')

    def test_negative_values_remain_valid_in_cartesian_charts(self):
        for chart_type in ('bar', 'line', 'scatter'):
            with self.subTest(chart_type=chart_type):
                data = valid_data()
                chart = data['findings'][0]['chart']
                chart['type'] = chart_type
                chart['datasets'][0]['data'] = ([{'x': -1, 'y': -2}] if chart_type == 'scatter'
                                                 else [-1, 0, 1])
                render_report.validate(data)

    def test_unknown_chart_type(self):
        data = valid_data()
        data['findings'][0]['chart']['type'] = 'pie'
        self.assert_invalid(data, r'findings\[0\]\.chart\.type: expected bar/line/doughnut/scatter')

    def test_table_row_with_wrong_cell_count(self):
        data = valid_data()
        data['table']['rows'][0] = ['only one cell']
        self.assert_invalid(data, r'table\.rows\[0\]: expected one cell per header')


class SampleReportTest(unittest.TestCase):
    """The example report embedded in the template must match sample/campaign-data-sample.csv."""

    @classmethod
    def setUpClass(cls):
        with CSV_PATH.open(encoding='utf-8', newline='') as handle:
            reader = csv.DictReader(handle)
            cls.columns = reader.fieldnames
            cls.rows = [dict(record, line=line) for line, record in enumerate(reader, start=2)]
        cls.template = TEMPLATE_PATH.read_text(encoding='utf-8')
        cls.embedded = json.loads(data_block(cls.template))

    def spend(self, rows): return sum(float(r['消耗']) for r in rows)
    def gmv(self, rows): return sum(float(r['GMV']) for r in rows)

    def channel_rows(self, channel):
        return [r for r in self.rows if r['渠道'] == channel]

    def kpi(self, label):
        return next(k for k in self.embedded['kpis'] if k['label'] == label)

    def test_embedded_data_is_valid(self):
        render_report.validate(self.embedded)

    def test_kpis_match_the_csv(self):
        total_spend, total_gmv = self.spend(self.rows), self.gmv(self.rows)
        returns = [float(r['退货率']) for r in self.rows if r['退货率'].strip()]
        self.assertEqual(self.kpi('总消耗')['value'], f'¥{total_spend:,.0f}')
        self.assertEqual(self.kpi('总 GMV')['value'], f'¥{total_gmv:,.0f}')
        self.assertEqual(self.kpi('整体 ROI')['value'], f'{total_gmv / total_spend:.2f}')
        self.assertEqual(self.kpi('平均退货率')['value'], f'{sum(returns) / len(returns) * 100:.1f}%')

    def test_meta_matches_the_csv(self):
        meta = self.embedded['meta']
        dates = sorted(r['日期'] for r in self.rows)
        self.assertEqual(meta['source'], CSV_PATH.name)
        self.assertEqual(meta['rows'], f'{len(self.rows)} 行 · {len(self.columns)} 列')
        self.assertEqual(meta['period'], f'{dates[0][:7]} ~ {dates[-1][:7]}')

    def test_channel_table_matches_the_csv(self):
        table_rows = {row[1]: row for row in self.embedded['table']['rows']}
        self.assertEqual(sorted(table_rows), sorted({r['渠道'] for r in self.rows}))
        for channel, row in table_rows.items():
            rows = self.channel_rows(channel)
            self.assertEqual(row[2], str(round(self.spend(rows))), channel)
            self.assertEqual(row[3], str(round(self.gmv(rows))), channel)
            self.assertEqual(row[4], f'{self.gmv(rows) / self.spend(rows):.2f}', channel)

    def test_channel_charts_match_the_csv(self):
        bar = self.embedded['findings'][0]['chart']
        doughnut = self.embedded['findings'][2]['chart']
        self.assertEqual(bar['type'], 'bar')
        self.assertEqual(doughnut['type'], 'doughnut')
        for chart, expected in (
            (bar, lambda rows: round(self.gmv(rows) / self.spend(rows), 2)),
            (doughnut, lambda rows: round(self.spend(rows))),
        ):
            for label, value in zip(chart['labels'], chart['datasets'][0]['data']):
                self.assertEqual(value, expected(self.channel_rows(label)), label)
        self.assertEqual(sum(doughnut['datasets'][0]['data']), round(self.spend(self.rows)))

    def test_budget_shares_in_the_text_match_the_csv(self):
        total_spend = self.spend(self.rows)
        share = lambda channel: f'{self.spend(self.channel_rows(channel)) / total_spend * 100:.0f}%'
        self.assertIn(share('信息流') + ' 预算', self.embedded['meta']['headline'])
        self.assertIn(share('搜索') + ' 预算', self.embedded['findings'][2]['body'])

    def test_scatter_points_match_the_csv(self):
        scatter = self.embedded['findings'][3]['chart']
        self.assertEqual(scatter['type'], 'scatter')
        expected = [{'x': round(float(r['消耗'])), 'y': round(float(r['退货率']) * 100, 1)}
                    for r in self.rows if r['退货率'].strip()]
        self.assertEqual(scatter['datasets'][0]['data'], expected)

    def test_missing_return_rate_rows_are_reported(self):
        missing = [r['line'] for r in self.rows if not r['退货率'].strip()]
        self.assertEqual(missing, [17, 26])  # tests/test-cases.md: "退货率列有 2 行缺失"
        note = next(w for w in self.embedded['watch'] if '退货率列有' in w)
        self.assertIn('第 17、26 行', note)
        self.assertIn(self.kpi('平均退货率')['value'], note)

    def test_template_is_what_build_sample_data_generates(self):
        builder = load_script('build_sample_data')
        self.assertEqual(self.embedded, builder.build_data(CSV_PATH))
        self.assertEqual(render_report.render(builder.build_data(CSV_PATH), self.template), self.template)


if __name__ == '__main__':
    unittest.main()
