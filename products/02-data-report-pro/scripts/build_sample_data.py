"""Rebuild the example report data from sample/campaign-data-sample.csv.

Every number in the example report embedded in references/report-template.html is
computed here from the CSV; nothing is typed by hand. The script is a maintenance
tool for the bundled sample, not an analysis tool for user data.

    python3 scripts/build_sample_data.py            # rewrite the example block in the template
    python3 scripts/build_sample_data.py --check    # exit 1 if the template is out of date
    python3 scripts/build_sample_data.py --data-out report-data.json
"""
import argparse
import calendar
import csv
import importlib.util
import itertools
import json
import math
import os
import sys
import tempfile
from collections import OrderedDict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / 'sample' / 'campaign-data-sample.csv'
TEMPLATE_PATH = ROOT / 'references' / 'report-template.html'

# Reference lines are industry rules of thumb (references/domain-knowledge.md), not facts in the CSV.
BREAKEVEN_ROI = 2.0
RETURN_WARN = 0.12
# What-if used in finding 3 / recs: move this share of the worst channel's spend to the best one.
SHIFT_SHARE = 0.5

COLUMNS = ('日期', '板块', '渠道', '消耗', 'GMV', 'ROI', '退货率')


def load_renderer():
    # Load by path: `python3 -I` does not put the script directory on sys.path.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('render_report', Path(__file__).resolve().with_name('render_report.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def premise(condition, message):
    """The example text states these facts; refuse to build if the CSV no longer supports them."""
    if not condition:
        raise ValueError('narrative premise failed: ' + message)


def csv_records(reader):
    try:
        yield from reader
    except csv.Error:
        raise ValueError(f'line {reader.line_num}: malformed CSV') from None


def read_rows(csv_path):
    with open(csv_path, encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle, strict=True)
        records = csv_records(reader)
        premise(tuple(next(records, [])) == COLUMNS, f'columns must be {COLUMNS}')
        rows = []
        for values in records:
            if not values:  # csv.DictReader also ignores blank physical lines.
                continue
            line = reader.line_num  # Last physical line of this CSV record, including quoted newlines.
            premise(len(values) == len(COLUMNS), f'line {line}: expected {len(COLUMNS)} columns')
            record = dict(zip(COLUMNS, values))

            def numeric(column):
                try:
                    value = float(record[column])
                except (TypeError, ValueError):
                    raise ValueError(f'line {line}: {column} must be a finite number') from None
                premise(math.isfinite(value), f'line {line}: {column} must be a finite number')
                return value

            spend, gmv, roi = numeric('消耗'), numeric('GMV'), numeric('ROI')
            premise(spend > 0, f'line {line}: spend must be positive')
            premise(gmv >= 0, f'line {line}: GMV must be nonnegative')
            premise(round(gmv / spend, 2) == roi, f'line {line}: ROI column differs from GMV / spend')
            ret = record['退货率'].strip()
            ret = numeric('退货率') if ret else None
            premise(ret is None or 0 <= ret <= 1, f'line {line}: return rate must be between 0 and 1')
            premise(bool(record['板块'].strip()), f'line {line}: board must be nonempty')
            premise(bool(record['渠道'].strip()), f'line {line}: channel must be nonempty')
            try:
                row_date = date.fromisoformat(record['日期'])
                if row_date.isoformat() != record['日期']:
                    raise ValueError
            except ValueError:
                raise ValueError(f'line {line}: date must be ISO YYYY-MM-DD') from None
            rows.append({
                'line': line,
                'date': row_date,
                'board': record['板块'],
                'channel': record['渠道'],
                'spend': spend,
                'gmv': gmv,
                'ret': ret,
            })
    premise(bool(rows), 'CSV must contain at least one data row')
    rows.sort(key=lambda r: (r['date'], r['line']))
    return rows, len(COLUMNS)


def num(value):
    value = float(value)
    return int(value) if value.is_integer() else round(value, 2)


def money(value):
    return f'¥{value:,.0f}'


def pct(value, digits=0):
    return f'{value * 100:.{digits}f}%'


def mean(values):
    return sum(values) / len(values)


def pearson(points):
    premise(len(points) >= 2, 'correlation requires at least two observed points')
    mx, my = mean([x for x, _ in points]), mean([y for _, y in points])
    sxy = sum((x - mx) * (y - my) for x, y in points)
    sxx = sum((x - mx) ** 2 for x, _ in points)
    syy = sum((y - my) ** 2 for _, y in points)
    premise(sxx > 0 and syy > 0, 'correlation requires variation in both dimensions')
    return sxy / math.sqrt(sxx * syy)


def channel_stats(rows, total_spend, total_gmv):
    groups = OrderedDict()
    for r in rows:
        groups.setdefault(r['channel'], []).append(r)
    stats = []
    for name, rs in groups.items():
        boards = {r['board'] for r in rs}
        premise(len(boards) == 1, f'channel {name} must belong to one board')
        spend, gmv = sum(r['spend'] for r in rs), sum(r['gmv'] for r in rs)
        rets = [r['ret'] for r in rs if r['ret'] is not None]
        stats.append({
            'name': name,
            'board': boards.pop(),
            'n': len(rs),
            'spend': spend,
            'gmv': gmv,
            'roi': gmv / spend,
            'spend_share': spend / total_spend,
            'gmv_share': gmv / total_gmv,
            'ret': mean(rets) if rets else None,
            'first_roi': round(rs[0]['gmv'] / rs[0]['spend'], 2),
            'last_roi': round(rs[-1]['gmv'] / rs[-1]['spend'], 2),
            'gap': max(0.0, BREAKEVEN_ROI * spend - gmv),
        })
    return sorted(stats, key=lambda s: (s['roi'], s['name']))


def monthly_roi(rows):
    months = OrderedDict()
    for r in rows:
        key = (r['date'].year, r['date'].month)
        months.setdefault(key, {'spend': 0.0, 'gmv': 0.0, 'last': r['date']})
        months[key]['spend'] += r['spend']
        months[key]['gmv'] += r['gmv']
        months[key]['last'] = max(months[key]['last'], r['date'])
    end = rows[-1]['date']
    result = []
    for (year, month), m in months.items():
        # Only the month holding the last row can be cut short; other months are just sampled sparsely.
        partial = (year, month) == (end.year, end.month) and end.day < calendar.monthrange(year, month)[1]
        result.append({
            'label': f'{year}-{month:02d}' + (f'（截至 {m["last"].day} 日）' if partial else ''),
            'short': f'{month} 月',
            'roi': m['gmv'] / m['spend'],
            'partial': partial,
            'last': m['last'],
        })
    return result


def build_data(csv_path=CSV_PATH, report_date=None):
    rows, n_columns = read_rows(csv_path)
    total_spend = sum(r['spend'] for r in rows)
    total_gmv = sum(r['gmv'] for r in rows)
    premise(total_gmv > 0, 'total GMV must be positive to compare channel shares')
    overall_roi = total_gmv / total_spend
    returns = [r['ret'] for r in rows if r['ret'] is not None]
    missing = [r['line'] for r in rows if r['ret'] is None]
    premise(len(returns) >= 2, 'at least two observed return rates are required')
    avg_return = mean(returns)

    channels = channel_stats(rows, total_spend, total_gmv)
    worst, best = channels[0], channels[-1]
    below = [c for c in channels if c['roi'] < BREAKEVEN_ROI]
    premise(overall_roi < BREAKEVEN_ROI, 'overall ROI must be below the break-even line')
    premise(avg_return > RETURN_WARN, 'average return rate must be above the warning line')
    premise(worst['roi'] < BREAKEVEN_ROI, 'worst channel must be below the break-even line')
    premise(worst['spend'] == max(c['spend'] for c in channels), 'worst channel must have the largest spend')
    premise(best['spend_share'] < best['gmv_share'], 'best channel must earn a larger GMV share than its spend share')
    premise(len(below) >= 2 and channels[1] in below, 'the two lowest-ROI channels must be below the break-even line')

    months = monthly_roi(rows)
    premise(len(months) >= 2 and all(a['roi'] > b['roi'] for a, b in itertools.pairwise(months)),
            'monthly ROI must fall month after month')
    declined = [c for c in channels if c['last_roi'] < c['first_roi']]
    premise(declined and all(c['last_roi'] <= c['first_roi'] for c in channels), 'no channel ROI may rise over time')

    points = [(r['spend'], r['ret']) for r in rows if r['ret'] is not None]
    corr = pearson(points)
    premise(corr >= 0.7, 'spend and return rate must be strongly correlated')
    top5 = sorted(rows, key=lambda r: -r['spend'])[:5]
    premise(len(top5) == 5 and all(r['ret'] is not None for r in top5),
            'five highest spends must have observed return rates')
    top_channels = {r['channel'] for r in top5}
    premise(len(top_channels) == 1, 'the five largest spends must come from one channel')
    top_channel = next(c for c in channels if c['name'] in top_channels)

    moved = worst['spend'] * SHIFT_SHARE
    shifted_roi = (total_gmv - moved * worst['roi'] + moved * best['roi']) / total_spend
    first_month, last_month = months[0], months[-1]
    drop = (first_month['roi'] - last_month['roi']) / first_month['roi']
    gap_total = sum(c['gap'] for c in below)
    others = [c for c in below if c is not worst]
    partial_month = next((m for m in reversed(months) if m['partial']), None)
    fewest = min(channels, key=lambda c: c['n'])
    first_date, last_date = rows[0]['date'], rows[-1]['date']

    data = OrderedDict()
    data['meta'] = {
        'kicker': f'投放数据复盘 · {first_date.year} Q{(first_date.month - 1) // 3 + 1}',
        'headline': f'{worst["name"]}吃掉 {pct(worst["spend_share"])} 预算，ROI 仅 {worst["roi"]:.2f}，'
                    f'低于盈亏线，建议削减并转投{best["name"]}',
        'summary': f'本期 {len(channels)} 个渠道整体 ROI {overall_roi:.2f}，低于盈亏线 {BREAKEVEN_ROI:.1f}。'
                   f'{worst["name"]}的 ROI 最低，而{best["name"]}的 ROI 达 {best["roi"]:.2f}、'
                   f'只占 {pct(best["spend_share"])} 预算，仍有放量空间。',
        'source': Path(csv_path).name,
        'rows': f'{len(rows)} 行 · {n_columns} 列',
        'period': f'{first_date:%Y-%m} ~ {last_date:%Y-%m}',
        'date': str(report_date or last_date),
    }
    data['kpis'] = [
        {'label': '总消耗', 'value': money(total_spend), 'delta': f'共 {len(rows)} 笔投放', 'dir': 'flat'},
        {'label': '总 GMV', 'value': money(total_gmv), 'delta': '含未剔除的退货金额', 'dir': 'flat'},
        {'label': '整体 ROI', 'value': f'{overall_roi:.2f}', 'delta': f'低于盈亏线 {BREAKEVEN_ROI:.1f}', 'dir': 'down'},
        {'label': '平均退货率', 'value': pct(avg_return, 1), 'delta': f'高于预警线 {pct(RETURN_WARN)}', 'dir': 'down'},
    ]

    other_text = '；'.join(f'{c["name"]} ROI {c["roi"]:.2f}，缺口 {money(c["gap"])}' for c in others)
    findings = []
    findings.append({
        'rank': '发现 1 · 最高优先级',
        'title': f'{worst["name"]} ROI 仅 {worst["roi"]:.2f}，是拖累整体 ROI 的主因',
        'body': f'{worst["name"]}消耗 {money(worst["spend"])}（占 {pct(worst["spend_share"])}），ROI 只有 {worst["roi"]:.2f}，'
                f'低于盈亏线 {BREAKEVEN_ROI:.1f}。按盈亏线算，这些消耗应产出 {money(BREAKEVEN_ROI * worst["spend"])} GMV，'
                f'实际 {money(worst["gmv"])}，缺口 {money(worst["gap"])}，占所有低于盈亏线渠道缺口的 {pct(worst["gap"] / gap_total)}。'
                + (f'其余低于盈亏线的渠道：{other_text}。' if others else ''),
        'sowhat': f'该渠道每投入 1 元只换回 ¥{worst["roi"]:.2f} GMV。建议本周内把{worst["name"]}预算削减约一半'
                  f'（约 {money(moved)}），再观察整体 ROI 是否向盈亏线靠近。',
        'chart': {
            'type': 'bar',
            'title': f'各渠道 ROI（盈亏线参考值 {BREAKEVEN_ROI:.1f}）',
            'labels': [c['name'] for c in channels],
            'datasets': [{'label': 'ROI', 'data': [round(c['roi'], 2) for c in channels]}],
        },
    })
    declined_text = '、'.join(f'{c["name"]}（{c["first_roi"]:.2f}→{c["last_roi"]:.2f}）' for c in declined)
    steady = len(channels) - len(declined)
    findings.append({
        'rank': '发现 2',
        'title': '整体 ROI 逐月走低：' + ' → '.join(f'{m["short"]} {m["roi"]:.2f}' for m in months),
        'body': f'整体 ROI 由 {first_month["roi"]:.2f} 降到 {last_month["roi"]:.2f}，约降 {pct(drop)}'
                + (f'（{last_month["short"]}只到 {last_month["last"].day} 日）' if last_month['partial'] else '')
                + f'。同期{declined_text}自身的 ROI 也在走低，其余 {steady} 个渠道持平，没有渠道回升。',
        'sowhat': f'趋势比单点更值得警惕：若继续走低，整体 ROI 会离盈亏线 {BREAKEVEN_ROI:.1f} 越来越远。'
                  '先排查是创意疲劳还是竞价环境变化，再决定是否加预算。',
        'chart': {
            'type': 'line',
            'title': '整体 ROI 逐月走势',
            'labels': [m['label'] for m in months],
            'datasets': [{'label': '整体 ROI', 'data': [round(m['roi'], 2) for m in months]}],
        },
    })
    low2 = channels[:2]
    findings.append({
        'rank': '发现 3',
        'title': '预算分配与产出错配',
        'body': f'{"与".join(c["name"] for c in low2)}合计拿走 {pct(sum(c["spend_share"] for c in low2))} 预算，'
                f'只贡献 {pct(sum(c["gmv_share"] for c in low2))} GMV；{best["name"]}只占 {pct(best["spend_share"])} 预算，'
                f'却贡献 {pct(best["gmv_share"])} GMV。',
        'sowhat': f'预算应从低效渠道向{best["name"]}倾斜。理论上把{worst["name"]}预算的一半（{money(moved)}）转投{best["name"]}，'
                  f'整体 ROI 可由 {overall_roi:.2f} 升到约 {shifted_roi:.2f}；这假设{best["name"]}的 ROI {best["roi"]:.2f} '
                  '在放量后不下降，实际通常会下降，宜分步试投。',
        'chart': {
            'type': 'doughnut',
            'title': '预算分配占比',
            'labels': [c['name'] for c in channels],
            'datasets': [{'label': '消耗', 'data': [num(c['spend']) for c in channels]}],
        },
    })
    findings.append({
        'rank': '发现 4',
        'title': f'消耗越高的投放，退货率越高（相关 r={corr:.2f}）',
        'body': f'{len(points)} 笔有退货率的投放里，单笔消耗与退货率强正相关。消耗最高的 5 笔全部来自{top_channel["name"]}，'
                f'该渠道平均退货率 {pct(top_channel["ret"])}，高于整体的 {pct(avg_return)} 和预警线 {pct(RETURN_WARN)}。',
        'sowhat': '相关不等于因果：高消耗和高退货率可能都来自渠道本身，而不是消耗拉高了退货。'
                  '建议先按渠道分层核对退货率，再判断冲量是否拉低了流量质量。',
        'chart': {
            'type': 'scatter',
            'title': '单次投放消耗 vs 退货率（%）',
            'xTitle': '消耗(元)',
            'yTitle': '退货率(%)',
            'datasets': [{'label': '投放', 'data': [{'x': num(x), 'y': round(y * 100, 1)} for x, y in points]}],
        },
    })
    data['findings'] = findings

    watch = [
        (f'退货率列有 {len(missing)} 行缺失（CSV 第 {"、".join(map(str, missing))} 行，行号含表头，占 {pct(len(missing) / len(rows))}），'
         f'平均退货率 {pct(avg_return, 1)} 是剔除缺失后按 {len(returns)} 行计算的，未补值。'
         if missing else f'退货率列无缺失，平均退货率 {pct(avg_return, 1)} 按全部 {len(returns)} 行计算。'),
        f'「{fewest["name"]}」渠道仅 {fewest["n"]} 笔投放，样本过小，其 ROI {fewest["roi"]:.2f} 的结论需更多数据验证。',
    ]
    if partial_month:
        watch.append(f'{partial_month["short"]}数据只到 {partial_month["last"]}，逐月 ROI 里的 {partial_month["short"]}是不完整月份，趋势需下月数据确认。')
    watch.append('GMV 含未剔除的退货金额，净 GMV 会更低，盈亏判断偏乐观。')
    watch.append(f'盈亏线 {BREAKEVEN_ROI:.1f} 与退货预警线 {pct(RETURN_WARN)} 是行业经验线（见 references/domain-knowledge.md），'
                 '不是这份数据给出的，实际请以贵方毛利和目标为准。')
    data['watch'] = watch

    data['recs'] = [
        {'t': f'本周内将{worst["name"]}预算削减 50%',
         'note': f'{worst["name"]} ROI {worst["roi"]:.2f} 低于盈亏线 {BREAKEVEN_ROI:.1f}，约 {money(moved)} 预算可重新分配'},
        {'t': f'把削减的预算分步转投{best["name"]}',
         'note': f'{best["name"]} ROI {best["roi"]:.2f} 为各渠道最高且仅占 {pct(best["spend_share"])} 预算，放量后盯住 ROI 是否下降'},
        {'t': '排查整体 ROI 逐月走低的根因（创意疲劳 / 竞价）',
         'note': f'{first_month["short"]} {first_month["roi"]:.2f} → {last_month["short"]} {last_month["roi"]:.2f}，加预算前先定位'},
        {'t': '对高消耗投放做退货归因',
         'note': f'消耗与退货率相关 r={corr:.2f}，先按渠道分层，验证冲量是否拉低流量质量'},
    ]

    table_rows = []
    for c in sorted(channels, key=lambda c: c['board']):
        row = [c['board'], c['name'], str(num(c['spend'])), str(num(c['gmv'])), f'{c["roi"]:.2f}',
               pct(c['ret']) if c['ret'] is not None else '—']
        if c['roi'] < BREAKEVEN_ROI:
            row.append('neg')
        table_rows.append(row)
    data['table'] = {'head': ['板块', '渠道', '消耗(元)', 'GMV(元)', 'ROI', '退货率'], 'rows': table_rows}
    return json.loads(json.dumps(data, ensure_ascii=False))


def embedded_data(renderer, template_text):
    if template_text.count(renderer.START) != 1:
        raise ValueError('Expected exactly one report-data block')
    block = template_text.split(renderer.START, 1)[1].split(renderer.END, 1)[0]
    return json.loads(block)


def write_atomic(path, text):
    # Exclusive, unpredictable name: never truncate an adjacent user file or follow its symlink.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
            temp = Path(handle.name)
            handle.write(text)
        # NamedTemporaryFile is 0600; keep the replaced file's mode (or the umask default).
        if path.exists():
            mode = path.stat().st_mode & 0o777
        else:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', type=Path, default=CSV_PATH)
    parser.add_argument('--template', type=Path, default=TEMPLATE_PATH)
    parser.add_argument('--check', action='store_true', help='only compare; exit 1 if the template differs')
    parser.add_argument('--data-out', type=Path, help='also write the data JSON here (never overwrites)')
    args = parser.parse_args(argv)

    renderer = load_renderer()
    data = build_data(args.csv)
    template = args.template.read_text(encoding='utf-8')
    html = renderer.render(data, template)
    if args.data_out:
        with args.data_out.open('x', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write('\n')
    if args.check:
        if html != template:
            print(f'{args.template} does not match {args.csv}; run build_sample_data.py to rebuild it.', file=sys.stderr)
            return 1
        print('example data matches the CSV')
        return 0
    if html != template:
        write_atomic(args.template, html)
        print(f'rewrote the example data in {args.template}')
    else:
        print('example data already up to date')
    return 0


if __name__ == '__main__':
    sys.exit(main())
