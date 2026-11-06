// Run the real template script with a small DOM and Chart stand-in.
// These tests check data/configuration flow; a browser must check layout and pixels.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../../references/report-template.html'), 'utf8');
const example = JSON.parse(html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this._text = '';
    this.listeners = {};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ''; this.children = nodes; }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(value) { throw new Error('Report content must use text nodes'); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  dispatch(type) { this.listeners[type]?.(); }
}

function render(data, online = true, failedCharts = [], cleanupFails = false) {
  const roots = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => {
    const node = new Element('div');
    node.id = match[1];
    return node;
  });
  const all = () => {
    const visit = node => [node, ...node.children.flatMap(visit)];
    return roots.flatMap(visit);
  };
  const document = {
    getElementById: id => all().find(node => node.id === id),
    createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element('#text'), {textContent: text}),
    querySelectorAll: selector => all().filter(node => node.className === selector.slice(1)),
  };
  document.getElementById('report-data').textContent = JSON.stringify(data);
  const charts = [];
  const destroyed = [];
  function Chart(canvas, config) {
    charts.push({canvas, config});
    if (failedCharts.includes(canvas.id)) throw new Error('private fixture detail');
  }
  Chart.getChart = canvas => ({destroy() {
    destroyed.push(canvas.id);
    if (cleanupFails) throw new Error('private cleanup detail');
  }});
  const context = {document, window: online ? {Chart} : {}};
  if (online) context.Chart = Chart;
  vm.runInNewContext(script, context, {timeout: 1000});
  const finishLoad = (success = true) => {
    if (success) { context.Chart = Chart; context.window.Chart = Chart; }
    document.getElementById('chart-library')?.dispatch(success ? 'load' : 'error');
  };
  return {document, charts, all, destroyed, finishLoad};
}

test('sample renders its KPIs, table, and all four chart configurations', () => {
  const {document, charts} = render(example);
  assert.equal(charts.length, 4);
  assert.equal(document.getElementById('kpi-row').children.length, 4);
  assert.equal(document.getElementById('tbl-body').children.length, 6);
  assert.equal(document.title, example.meta.headline);
  const scatter = charts.find(chart => chart.config.type === 'scatter').config;
  assert.equal(scatter.options.scales.x.title.text, '消耗(元)');
  assert.equal(scatter.options.scales.y.title.text, '退货率(%)');
});

test('scatter axes use the supplied business names and units', () => {
  const data = structuredClone(example);
  data.findings = [data.findings[3]];
  data.findings[0].chart.xTitle = '温度(°C)';
  data.findings[0].chart.yTitle = '用电量(kWh)';
  const {charts} = render(data);
  assert.equal(charts[0].config.options.scales.x.title.text, '温度(°C)');
  assert.equal(charts[0].config.options.scales.y.title.text, '用电量(kWh)');
});

test('a chart with no axis titles does not assume a spend axis', () => {
  const data = structuredClone(example);
  data.findings = [data.findings[3]];
  delete data.findings[0].chart.xTitle;
  delete data.findings[0].chart.yTitle;
  const {charts} = render(data);
  assert.equal(charts[0].config.options.scales.x.title, undefined);
  assert.equal(charts[0].config.options.scales.y.title, undefined);
});

for (const type of ['bar', 'line', 'scatter']) {
  test(`${type} makes multiple datasets distinguishable and labels the legend`, () => {
    const data = structuredClone(example);
    data.findings = [data.findings[type === 'scatter' ? 3 : 0]];
    const chart = data.findings[0].chart;
    chart.type = type;
    chart.datasets.push({...structuredClone(chart.datasets[0]), label: '对比组'});
    const {charts} = render(data);
    const config = charts[0].config;
    assert.equal(config.options.plugins.legend.display, true);
    const [first, second] = config.data.datasets;
    assert.notEqual(first.borderColor, second.borderColor);
    assert.notDeepEqual(first.backgroundColor, second.backgroundColor);
    assert.equal(second.label, '对比组');
  });
}

test('single bar charts retain category colors and do not add a redundant legend', () => {
  const {charts} = render(example);
  const config = charts[0].config;
  assert.equal(config.options.plugins.legend.display, false);
  assert.equal(new Set(config.data.datasets[0].backgroundColor).size, config.data.labels.length);
});

test('a failed chart has a readable fallback and does not stop later charts', () => {
  const {document, charts, all, destroyed} = render(example, true, ['chart-1']);
  assert.equal(charts.length, 4);
  assert.deepEqual(destroyed, ['chart-1']);
  assert.equal(document.getElementById('tbl-body').children.length, 6);
  const boxes = all().filter(node => node.className === 'chart-box');
  assert.match(boxes[1].textContent, /图表暂时无法显示/);
  assert.equal(boxes[1].children[0].tagName, 'P');
  assert.ok(!boxes[1].textContent.includes('private fixture detail'));
  assert.equal(boxes[2].children[0].tagName, 'CANVAS');
});

test('even a chart cleanup failure does not stop the remaining report', () => {
  const {charts, all} = render(example, true, ['chart-0'], true);
  assert.equal(charts.length, 4);
  const boxes = all().filter(node => node.className === 'chart-box');
  assert.match(boxes[0].textContent, /图表暂时无法显示/);
  assert.equal(boxes[3].children[0].tagName, 'CANVAS');
});

test('offline rendering retains content and replaces every canvas with an explanation', () => {
  const {document, charts, all} = render(example, false);
  assert.equal(charts.length, 0);
  assert.equal(document.getElementById('tbl-body').children.length, 6);
  const boxes = all().filter(node => node.className === 'chart-box');
  assert.equal(boxes.length, 4);
  for (const box of boxes) {
    assert.match(box.textContent, /图表需联网加载 Chart.js/);
    assert.equal(box.children[0].tagName, 'P');
  }
});

test('the chart library cannot block parsing the report body', () => {
  assert.match(html, /<script\b(?=[^>]*\bid="chart-library")(?=[^>]*\bdefer\b)[^>]*\bsrc="https:\/\/cdn\.jsdelivr\.net\//);
});

test('a delayed chart library draws charts after the report text is already available', () => {
  const {document, charts, all, finishLoad} = render(example, false);
  assert.equal(document.getElementById('r-headline').textContent, example.meta.headline);
  assert.equal(document.getElementById('tbl-body').children.length, 6);
  assert.equal(charts.length, 0);
  const disclosure = all().find(node => node.tagName === 'DETAILS');
  disclosure.open = true;
  finishLoad();
  assert.equal(charts.length, 4);
  assert.equal(all().find(node => node.tagName === 'DETAILS'), disclosure);
  assert.equal(disclosure.open, true);
  const boxes = all().filter(node => node.className === 'chart-box');
  assert.ok(boxes.every(box => box.children[0].tagName === 'CANVAS'));
});

test('library load failure keeps the full textual report available', () => {
  const {document, charts, all, finishLoad} = render(example, false);
  finishLoad(false);
  assert.equal(charts.length, 0);
  assert.equal(document.getElementById('tbl-body').children.length, 6);
  assert.ok(all().filter(node => node.className === 'chart-box')
    .every(box => /图表需联网加载 Chart.js/.test(box.textContent)));
});

test('every chart exposes its underlying data in a labeled disclosure table, including offline', () => {
  const {all} = render(example, false);
  const details = all().filter(node => node.tagName === 'DETAILS');
  assert.equal(details.length, example.findings.length);
  details.forEach((detail, i) => {
    const chart = example.findings[i].chart;
    assert.equal(detail.children[0].tagName, 'SUMMARY');
    assert.match(detail.children[0].textContent, /查看图表数据/);
    const table = detail.children[1];
    assert.equal(table.tagName, 'TABLE');
    assert.equal(table.children[0].tagName, 'CAPTION');
    assert.equal(table.children[0].textContent, chart.title);
    const header = table.children[1].children[0].children;
    assert.ok(header.every(cell => cell.tagName === 'TH' && cell.attributes.scope === 'col'));
    const actual = table.children[2].children.map(row => row.children.map(cell => cell.textContent));
    const expected = chart.type === 'scatter'
      ? chart.datasets.flatMap(ds => ds.data.map(point => [ds.label, String(point.x), String(point.y)]))
      : chart.labels.map((label, j) => [String(label), ...chart.datasets.map(ds => String(ds.data[j]))]);
    assert.deepEqual(actual, expected);
  });
});

test('chart data table headers preserve axis names and multiple series labels', () => {
  const data = structuredClone(example);
  data.findings[0].chart.datasets.push({label: '对比组', data: [1, 2, 3, 4, 5, 6]});
  const {all} = render(data);
  const tables = all().filter(node => node.tagName === 'DETAILS').map(detail => detail.children[1]);
  const labels = table => table.children[1].textContent;
  assert.ok(labels(tables[0]).includes('对比组'));
  assert.ok(labels(tables[3]).includes('消耗(元)'));
  assert.ok(labels(tables[3]).includes('退货率(%)'));
});

test('report content stays literal text throughout the real rendering script', () => {
  const data = structuredClone(example);
  const text = '<img src=x onerror=alert(1)>&正文';
  data.meta.headline = text;
  data.findings[0].title = text;
  data.watch[0] = text;
  data.table.rows[0][0] = text;
  data.findings[0].chart.labels[0] = text;
  data.findings[0].chart.datasets[0].label = text;
  const {document, all} = render(data);
  assert.equal(document.getElementById('r-headline').textContent, text);
  assert.ok(document.getElementById('findings').textContent.includes(text));
  assert.ok(document.getElementById('watch-list').textContent.includes(text));
  assert.ok(document.getElementById('tbl-body').textContent.includes(text));
  assert.equal(all().filter(node => node.tagName === 'IMG').length, 0);
});

test('every chart canvas exposes its title to assistive technology', () => {
  const {charts} = render(example);
  charts.forEach(({canvas}, i) => {
    assert.equal(canvas.attributes.role, 'img');
    assert.equal(canvas.attributes['aria-label'], example.findings[i].chart.title);
  });
});

test('secondary body text meets WCAG AA contrast on its page and card backgrounds', () => {
  const token = name => html.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i'))[1];
  const luminance = hex => {
    const linear = hex.slice(1).match(/../g).map(part => {
      const channel = parseInt(part, 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  };
  const foreground = luminance(token('ink-faint'));
  for (const background of ['bg', 'paper']) {
    const surface = luminance(token(background));
    const ratio = (Math.max(foreground, surface) + 0.05) / (Math.min(foreground, surface) + 0.05);
    assert.ok(ratio >= 4.5, `secondary text on ${background}: ${ratio.toFixed(2)} < 4.5`);
  }
});
