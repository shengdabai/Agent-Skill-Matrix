"""Embed analyzed JSON safely; this script does not perform data analysis."""
import argparse
import json
import math
from pathlib import Path

START = '<script id="report-data" type="application/json">'
END = '</script>'
MAX_SAFE_INTEGER = 2**53 - 1  # Report JSON is parsed as JavaScript Number in the browser.


def validate(data):
    def require(condition, path, expected):
        if not condition:
            raise ValueError(f'{path}: expected {expected}')
    def obj(value, path):
        require(isinstance(value, dict), path, 'object')
    def array(value, path):
        require(isinstance(value, list), path, 'array')
    def text(value, path):
        require(isinstance(value, str), path, 'string')
    def number(value, path):
        require(type(value) in (int, float) and (type(value) is int or math.isfinite(value)), path, 'finite number')
        require(abs(value) <= MAX_SAFE_INTEGER, path, 'number within JavaScript safe integer range; encode larger values as strings')
    def scalar(value, path):
        if not isinstance(value, str):
            number(value, path)
    obj(data, 'data')
    obj(data.get('meta'), 'meta')
    for key in ('kicker', 'headline', 'summary', 'source', 'rows', 'period', 'date'):
        scalar(data['meta'].get(key), f'meta.{key}')
    for key in ('kpis', 'findings', 'watch', 'recs'):
        array(data.get(key), key)
    for i, kpi in enumerate(data['kpis']):
        path = f'kpis[{i}]'
        obj(kpi, path)
        for key in ('label', 'value', 'delta'):
            scalar(kpi.get(key), f'{path}.{key}')
        require(kpi.get('dir') in ('up', 'down', 'flat'), f'{path}.dir', 'up/down/flat')
    for i, item in enumerate(data['watch']):
        text(item, f'watch[{i}]')
    for i, item in enumerate(data['recs']):
        obj(item, f'recs[{i}]')
        text(item.get('t'), f'recs[{i}].t')
        text(item.get('note', ''), f'recs[{i}].note')
    obj(data.get('table'), 'table')
    head, rows = data['table'].get('head'), data['table'].get('rows')
    array(head, 'table.head')
    array(rows, 'table.rows')
    for i, cell in enumerate(head):
        scalar(cell, f'table.head[{i}]')
    for i, row in enumerate(rows):
        path = f'table.rows[{i}]'
        array(row, path)
        require(len(row) == len(head) or (len(row) == len(head) + 1 and row[-1] == 'neg'), path, 'one cell per header, with optional neg flag')
        for j, cell in enumerate(row):
            scalar(cell, f'{path}[{j}]')
    for i, item in enumerate(data['findings']):
        path = f'findings[{i}]'
        obj(item, path)
        for key in ('rank', 'title', 'body', 'sowhat'):
            text(item.get(key), f'{path}.{key}')
        chart = item.get('chart')
        obj(chart, path + '.chart')
        require(chart.get('type') in ('bar', 'line', 'doughnut', 'scatter'), path + '.chart.type', 'bar/line/doughnut/scatter')
        text(chart.get('title'), path + '.chart.title')
        for key in ('xTitle', 'yTitle'):
            if key in chart:
                text(chart[key], f'{path}.chart.{key}')
        if chart['type'] != 'scatter':
            array(chart.get('labels'), path + '.chart.labels')
            require(bool(chart['labels']), path + '.chart.labels', 'nonempty array')
            for j, label in enumerate(chart['labels']):
                scalar(label, f'{path}.chart.labels[{j}]')
        array(chart.get('datasets'), path + '.chart.datasets')
        require(bool(chart['datasets']), path + '.chart.datasets', 'nonempty array')
        for j, dataset in enumerate(chart['datasets']):
            ds_path = f'{path}.chart.datasets[{j}]'
            obj(dataset, ds_path)
            text(dataset.get('label'), ds_path + '.label')
            array(dataset.get('data'), ds_path + '.data')
            require(bool(dataset['data']), ds_path + '.data', 'nonempty array')
            if chart['type'] != 'scatter':
                require(len(dataset['data']) == len(chart['labels']), ds_path + '.data', 'same length as labels')
            for k, value in enumerate(dataset['data']):
                value_path = f'{ds_path}.data[{k}]'
                if chart['type'] == 'scatter':
                    obj(value, value_path)
                    number(value.get('x'), value_path + '.x')
                    number(value.get('y'), value_path + '.y')
                else:
                    number(value, value_path)
                    if chart['type'] == 'doughnut':
                        require(value >= 0, value_path, 'nonnegative doughnut value')
            if chart['type'] == 'doughnut':
                require(any(value > 0 for value in dataset['data']), ds_path + '.data', 'positive doughnut total')


def render(data, template):
    validate(data)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
    for char in ('<', '>', '&', '\u2028', '\u2029'):
        encoded = encoded.replace(char, '\\u%04x' % ord(char))
    if template.count(START) != 1:
        raise ValueError('Expected exactly one report-data block')
    before, tail = template.split(START, 1)
    _, after = tail.split(END, 1)
    return before + START + '\n' + encoded + '\n' + END + after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    template = Path(__file__).resolve().parents[1] / 'references/report-template.html'
    def reject_constant(value):
        raise ValueError(f'Non-finite JSON value: {value}')
    data = json.loads(args.data.read_text(encoding='utf-8'), parse_constant=reject_constant)
    html = render(data, template.read_text(encoding='utf-8'))
    # Validate UTF-8 before creating the file, so malformed Unicode leaves no empty deliverable.
    encoded = html.encode('utf-8')
    # An existing deliverable is never silently overwritten.
    with args.output.open('xb') as output:
        output.write(encoded)


if __name__ == '__main__':
    main()
