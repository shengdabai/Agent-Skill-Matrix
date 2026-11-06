"""Input and narrative regressions for the bundled sample maintenance tool."""
import csv
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_sample_data', SCRIPTS / 'build_sample_data.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SampleInputTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'sample.csv'
        self.rows = list(csv.reader(io.StringIO(builder.CSV_PATH.read_text(encoding='utf-8'))))

    def save(self):
        with self.path.open('w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows(self.rows)

    def test_rejects_non_rectangular_rows(self):
        for row in (self.rows[1] + ['unrecognized column'], self.rows[1][:-1]):
            with self.subTest(row=row):
                self.rows[1] = row
                self.save()
                with self.assertRaisesRegex(ValueError, r'line 2: expected 7 columns'):
                    builder.read_rows(self.path)

    def test_rejects_non_finite_numeric_fields(self):
        for index in (3, 4, 5, 6):
            for token in ('nan', 'inf', '-inf', '1e999'):
                with self.subTest(column=index, token=token):
                    original = self.rows[1][index]
                    self.rows[1][index] = token
                    self.save()
                    with self.assertRaisesRegex(ValueError, r'line 2: .*finite number'):
                        builder.read_rows(self.path)
                    self.rows[1][index] = original

    def test_rejects_invalid_return_rate(self):
        for value in ('-0.1', '1.01', '18'):
            with self.subTest(value=value):
                self.rows[1][6] = value
                self.save()
                with self.assertRaisesRegex(ValueError, r'line 2: return rate must be between 0 and 1'):
                    builder.read_rows(self.path)

    def test_rejects_negative_gmv_even_when_roi_agrees(self):
        self.rows[1][4:6] = ['-16120', '-3.10']
        self.save()
        with self.assertRaisesRegex(ValueError, r'line 2: GMV must be nonnegative'):
            builder.read_rows(self.path)

    def test_rejects_blank_dimensions_and_bad_dates(self):
        for index, token, message in ((1, ' ', 'board must be nonempty'),
                                      (2, '', 'channel must be nonempty'),
                                      (0, '2026-02-30', 'date must be ISO YYYY-MM-DD')):
            with self.subTest(column=index):
                original = self.rows[1][index]
                self.rows[1][index] = token
                self.save()
                with self.assertRaisesRegex(ValueError, 'line 2: ' + message):
                    builder.read_rows(self.path)
                self.rows[1][index] = original

    def test_rejects_non_calendar_date_spellings(self):
        for token in ('20260403', '2026-W14-5'):
            with self.subTest(date=token):
                self.rows[1][0] = token
                self.save()
                with self.assertRaisesRegex(ValueError, 'date must be ISO YYYY-MM-DD'):
                    builder.read_rows(self.path)

    def test_rejects_unclosed_csv_quotes(self):
        self.path.write_text(','.join(builder.COLUMNS) + '\n"unclosed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, r'line 2: malformed CSV'):
            builder.read_rows(self.path)

    def test_missing_return_rate_uses_physical_csv_line_numbers(self):
        # One quoted field spans two physical lines, and a blank line is ignored.
        self.rows[1][2] += '\nquoted continuation'
        self.rows.insert(2, [])
        self.save()
        rows, _ = builder.read_rows(self.path)
        self.assertEqual([r['line'] for r in rows if r['ret'] is None], [19, 28])

    def test_all_zero_gmv_fails_with_a_narrative_error(self):
        for row in self.rows[1:]:
            row[4:6] = ['0', '0']
        self.save()
        with self.assertRaisesRegex(ValueError, 'total GMV must be positive'):
            builder.build_data(self.path)

    def test_header_only_csv_is_rejected_before_arithmetic(self):
        self.rows = self.rows[:1]
        self.save()
        with self.assertRaisesRegex(ValueError, 'CSV must contain at least one data row'):
            builder.build_data(self.path)

    def test_missing_return_rates_are_not_silently_zero_filled(self):
        for row in self.rows[1:]:
            row[6] = ''
        self.save()
        with self.assertRaisesRegex(ValueError, 'at least two observed return rates'):
            builder.build_data(self.path)

    def test_five_spend_claim_rejects_only_two_observed_return_rates(self):
        observed = 0
        for row in self.rows[1:]:
            if row[2] == '信息流' and observed < 2:
                observed += 1
            else:
                row[6] = ''
        self.save()
        with self.assertRaisesRegex(ValueError, 'five highest spends must have observed return rates'):
            builder.build_data(self.path)

    def test_five_spend_claim_does_not_skip_a_missing_high_spend(self):
        # An unobserved row from another channel must not disappear from the top five.
        row = max((row for row in self.rows[1:] if row[2] == '短视频'), key=lambda row: float(row[3]))
        row[3:7] = ['22000', '37400', '1.70', '']
        self.save()
        with self.assertRaisesRegex(ValueError, 'five highest spends must have observed return rates'):
            builder.build_data(self.path)

    def test_complete_return_rates_have_no_empty_missing_row_reference(self):
        for row in self.rows[1:]:
            if not row[6]:
                row[6] = '0.10'
        self.save()
        data = builder.build_data(self.path)
        note = data['watch'][0]
        self.assertIn('无缺失', note)
        self.assertIn('28 行', note)
        self.assertNotIn('CSV 第', note)

    def test_pearson_requires_two_nonconstant_dimensions(self):
        for points in ([], [(1, 0.1)], [(1, 0.1), (1, 0.2)], [(1, 0.1), (2, 0.1)]):
            with self.subTest(points=points):
                with self.assertRaisesRegex(ValueError, 'correlation requires'):
                    builder.pearson(points)

    def test_atomic_write_preserves_an_existing_tmp_file(self):
        target = self.path.with_suffix('.html')
        target.write_text('old template', encoding='utf-8')
        adjacent = target.with_name(target.name + '.tmp')
        adjacent.write_text('unrelated user content', encoding='utf-8')
        builder.write_atomic(target, 'new template')
        self.assertEqual(target.read_text(encoding='utf-8'), 'new template')
        self.assertEqual(adjacent.read_text(encoding='utf-8'), 'unrelated user content')

    def test_atomic_write_does_not_follow_an_adjacent_tmp_symlink(self):
        target = self.path.with_suffix('.html')
        unrelated = self.path.with_name('unrelated.txt')
        unrelated.write_text('preserve me', encoding='utf-8')
        adjacent = target.with_name(target.name + '.tmp')
        adjacent.symlink_to(unrelated.name)
        builder.write_atomic(target, 'new template')
        self.assertTrue(adjacent.is_symlink())
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'preserve me')

    def test_atomic_write_cleans_only_its_own_temp_on_replace_failure(self):
        target = self.path.with_suffix('.html')
        target.write_text('old template', encoding='utf-8')
        before = set(target.parent.iterdir())
        with mock.patch.object(builder.os, 'replace', side_effect=OSError('fixture failure')):
            with self.assertRaisesRegex(OSError, 'fixture failure'):
                builder.write_atomic(target, 'new template')
        self.assertEqual(target.read_text(encoding='utf-8'), 'old template')
        self.assertEqual(set(target.parent.iterdir()), before)

    def test_atomic_write_keeps_the_original_on_encoding_failure(self):
        target = self.path.with_suffix('.html')
        target.write_text('old template', encoding='utf-8')
        before = set(target.parent.iterdir())
        with self.assertRaises(UnicodeEncodeError):
            builder.write_atomic(target, '\ud800')
        self.assertEqual(target.read_text(encoding='utf-8'), 'old template')
        self.assertEqual(set(target.parent.iterdir()), before)


if __name__ == '__main__':
    unittest.main()
