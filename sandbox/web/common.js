/* Shared helpers for every app in the sandbox. */
const App = {
  state: null,      // server state: git, jira status, dag runs, query history, context
  apps: {},         // app modules register here: { mount(el), show(), refresh() }
  current: null,
};

async function api(path, body) {
  // Relative URLs, so the app works both at / (local) and under /practice/app/ (the site).
  // X-Sandbox marks the call as coming from this page (the site's proxy requires it on POSTs).
  const headers = { 'X-Sandbox': '1' };
  const opts = body === undefined ? { headers } : {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  const res = await fetch(path.replace(/^\//, ''), opts);
  const data = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
  if (!res.ok && data.ok === undefined) data.ok = false;
  return data;
}

async function refreshState() {
  App.state = await api('/api/state');
  return App.state;
}

/* h('div.cls#id', {attrs, onclick}, children...) */
function h(tag, attrs, ...children) {
  const [, name, rest] = tag.match(/^([a-z0-9]+)?(.*)$/i);
  const node = document.createElement(name || 'div');
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === '.') node.classList.add(part.slice(1));
    else node.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

let toastTimer;
function toast(msg, kind = 'info') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ''), 3500);
}

function modal(title, body, actions = []) {
  const root = document.getElementById('modal-root');
  const close = () => root.replaceChildren();
  const box = h('div.modal',
    h('div.modal-head', h('span', title), h('button.modal-x', { onclick: close, 'aria-label': 'Close' }, '×')),
    h('div.modal-body', body),
    actions.length ? h('div.modal-foot', actions.map(a => h(`button.${a.kind || 'btn'}`, {
      onclick: async () => { if ((await a.onclick?.()) !== false) close(); },
    }, a.label))) : null,
  );
  root.replaceChildren(h('div.modal-backdrop', { onclick: e => e.target === e.currentTarget && close() }, box));
  return close;
}

function confirmModal(title, text, label = 'Confirm') {
  return new Promise(resolve => {
    const close = modal(title, h('p', text), [
      { label: 'Cancel', kind: 'btn', onclick: () => resolve(false) },
      { label, kind: 'btn.primary', onclick: () => resolve(true) },
    ]);
    void close;
  });
}

function spinner(text) {
  return h('div.loading', h('span.spin'), text || 'Working...');
}

function timeAgo(ts) {
  if (!ts) return '';
  const sec = (Date.now() - new Date(ts.replace(' ', 'T')).getTime()) / 1000;
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} hr ago`;
  return `${Math.round(sec / 86400)} days ago`;
}

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(`atl101.${key}`); return v == null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`atl101.${key}`, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

/* Jinja highlighting on top of SQL for dbt files. */
if (window.CodeMirror && CodeMirror.overlayMode) {
  CodeMirror.defineMode('dbt-sql', config => CodeMirror.overlayMode(
    CodeMirror.getMode(config, 'text/x-sql'),
    {
      token(stream, state) {
        if (state.inJinja) {
          if (stream.match(state.inJinja)) { state.inJinja = null; return 'jinja-delim'; }
          stream.next();
          return 'jinja';
        }
        if (stream.match('{{')) { state.inJinja = '}}'; return 'jinja-delim'; }
        if (stream.match('{%')) { state.inJinja = '%}'; return 'jinja-delim'; }
        if (stream.match('{#')) { state.inJinja = '#}'; return 'jinja-comment'; }
        while (stream.next() != null && !stream.match(/^\{[{%#]/, false)) { /* skip */ }
        return null;
      },
      startState: () => ({ inJinja: null }),
    },
  ));
}

function modeFor(path) {
  if (/\.ya?ml$/.test(path)) return 'yaml';
  if (/\.py$/.test(path)) return 'python';
  if (/\.json$/.test(path)) return { name: 'javascript', json: true };
  if (/\.sql$/.test(path)) return 'dbt-sql';
  return null;
}

/* CodeMirror with a plain textarea fallback when the CDN is unreachable. */
function makeEditor(parent, { value = '', mode = 'text/x-sql', readOnly = false, extraKeys = {}, hint } = {}) {
  if (!window.CodeMirror) {
    const ta = h('textarea.fallback-editor', { spellcheck: 'false' });
    ta.value = value;
    if (readOnly) ta.readOnly = true;
    parent.append(ta);
    const handlers = [];
    ta.addEventListener('input', () => handlers.forEach(fn => fn()));
    ta.addEventListener('keydown', e => {
      const key = `${e.ctrlKey || e.metaKey ? 'Ctrl-' : ''}${e.shiftKey ? 'Shift-' : ''}${e.key === 'Enter' ? 'Enter' : e.key.toUpperCase()}`;
      if (extraKeys[key]) { e.preventDefault(); extraKeys[key](); }
    });
    return {
      getValue: () => ta.value, setValue: v => (ta.value = v), on: (ev, fn) => ev === 'change' && handlers.push(fn),
      getSelection: () => ta.value.slice(ta.selectionStart, ta.selectionEnd),
      cursorIndex: () => ta.selectionStart, refresh() {}, focus: () => ta.focus(),
      setOption(k, v) { if (k === 'readOnly') ta.readOnly = !!v; }, cm: null,
    };
  }
  const cm = CodeMirror(parent, {
    value, mode, readOnly, lineNumbers: true, matchBrackets: true, autoCloseBrackets: true,
    styleActiveLine: true, indentUnit: 4, tabSize: 4, indentWithTabs: false, lineWrapping: false,
    extraKeys: {
      'Ctrl-/': 'toggleComment', 'Cmd-/': 'toggleComment',
      Tab: c => c.somethingSelected() ? c.indentSelection('add') : c.replaceSelection('    ', 'end'),
      ...(hint ? { 'Ctrl-Space': c => c.showHint({ hint: CodeMirror.hint.sql, tables: hint(), completeSingle: false }) } : {}),
      ...extraKeys,
    },
  });
  if (hint) {
    cm.on('inputRead', (c, change) => {
      if (change.text[0] === '.' || /^[a-zA-Z_]$/.test(change.text[0]) && c.getTokenAt(c.getCursor()).string.length >= 2) {
        c.showHint({ hint: CodeMirror.hint.sql, tables: hint(), completeSingle: false });
      }
    });
  }
  return {
    cm,
    getValue: () => cm.getValue(), setValue: v => cm.setValue(v), on: (ev, fn) => cm.on(ev, fn),
    getSelection: () => cm.getSelection(), cursorIndex: () => cm.indexFromPos(cm.getCursor()),
    refresh: () => cm.refresh(), focus: () => cm.focus(), setOption: (k, v) => cm.setOption(k, v),
  };
}

/* Split SQL into statements with their offsets (quotes and comments respected). */
function splitStatements(sql) {
  const out = [];
  let start = 0, quote = null;
  for (let i = 0; i < sql.length; i++) {
    const c = sql[i];
    if (quote) { if (c === quote) quote = null; continue; }
    if (c === "'" || c === '"') { quote = c; continue; }
    if (c === '-' && sql[i + 1] === '-') { const e = sql.indexOf('\n', i); i = e === -1 ? sql.length : e; continue; }
    if (c === '/' && sql[i + 1] === '*') { const e = sql.indexOf('*/', i + 2); i = e === -1 ? sql.length : e + 1; continue; }
    if (c === ';') { out.push({ start, end: i + 1, text: sql.slice(start, i + 1) }); start = i + 1; }
  }
  out.push({ start, end: sql.length, text: sql.slice(start) });
  const meaningful = s => s.text.replace(/--[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/;/g, '').trim();
  return out.filter(meaningful);
}

/* Snowflake-style results grid. columns: [{name,type}], rows: [[...]] */
function typeIcon(type) {
  const t = String(type || '').toUpperCase();
  if (t.startsWith('NUMBER') || t === 'FLOAT') return h('span.ti.num', '#');
  if (t.startsWith('DATE') || t.startsWith('TIMESTAMP')) return h('span.ti.date', '◷');
  if (t === 'BOOLEAN') return h('span.ti', '◐');
  return h('span.ti.txt', 'Aa');
}

function renderGrid(columns, rows, { onColumn, selected } = {}) {
  const table = h('table.grid',
    h('thead', h('tr', h('th.rownum', ''), columns.map((c, i) =>
      h(`th${selected === i ? '.sel' : ''}`, { onclick: () => onColumn?.(i), title: c.type }, typeIcon(c.type), h('span', c.name))))),
    h('tbody', rows.map((r, ri) => h('tr', h('td.rownum', ri + 1), r.map((v, ci) => {
      const t = String(columns[ci]?.type || '');
      const cls = v === null ? '.null' : (t.startsWith('NUMBER') || t === 'FLOAT' ? '.num' : '');
      return h(`td${cls}${selected === ci ? '.sel' : ''}`, { title: v === null ? 'NULL' : String(v) }, v === null ? 'NULL' : String(v));
    })))),
  );
  return h('div.grid-wrap', table);
}

function columnStats(columns, rows, i) {
  const vals = rows.map(r => r[i]);
  const nonNull = vals.filter(v => v !== null);
  const distinct = new Set(nonNull.map(String));
  const t = String(columns[i].type);
  const stats = [['Type', t], ['Filled', `${rows.length ? Math.round(nonNull.length / rows.length * 100) : 0}%`], ['Distinct', distinct.size]];
  if (nonNull.length && (t.startsWith('NUMBER') || t === 'FLOAT')) {
    const nums = nonNull.map(Number);
    stats.push(['Min', Math.min(...nums)], ['Max', Math.max(...nums)]);
  } else if (nonNull.length) {
    const sorted = [...distinct].sort();
    stats.push(['Min', sorted[0]], ['Max', sorted[sorted.length - 1]]);
  }
  const counts = {};
  nonNull.forEach(v => (counts[v] = (counts[v] || 0) + 1));
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
  return { stats, top, total: rows.length };
}

function toCsv(columns, rows) {
  const q = v => v === null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
  return [columns.map(c => q(c.name)).join(','), ...rows.map(r => r.map(q).join(','))].join('\n');
}

function download(name, text) {
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
}

/* Unified diff -> GitHub-style table rows. */
function renderDiff(diffText) {
  const rows = [];
  let oldNo = 0, newNo = 0;
  for (const line of diffText.split('\n')) {
    if (line.startsWith('---') || line.startsWith('+++')) continue;
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldNo = +hunk[1]; newNo = +hunk[2];
      rows.push(h('tr.hunk', h('td.ln', '…'), h('td.ln', '…'), h('td.code', line)));
      continue;
    }
    if (line.startsWith('+')) rows.push(h('tr.add', h('td.ln', ''), h('td.ln', newNo++), h('td.code', h('span.sign', '+'), line.slice(1))));
    else if (line.startsWith('-')) rows.push(h('tr.del', h('td.ln', oldNo++), h('td.ln', ''), h('td.code', h('span.sign', '-'), line.slice(1))));
    else rows.push(h('tr', h('td.ln', oldNo++), h('td.ln', newNo++), h('td.code', h('span.sign', ' '), line.slice(1))));
  }
  return h('table.diff', h('tbody', rows));
}

/* Tiny markdown for PR bodies and Jira text: headings, bold, code, lists, paragraphs. */
function md(text) {
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  const out = [];
  let list = null;
  for (const line of String(text || '').split('\n')) {
    const li = line.match(/^\s*[-*] (.*)/) || line.match(/^\s*\d+\. (.*)/);
    if (li) { if (!list) { list = []; out.push(list); } list.push(`<li>${inline(li[1])}</li>`); continue; }
    list = null;
    const hd = line.match(/^(#{1,4}) (.*)/);
    if (hd) out.push(`<h${hd[1].length + 2}>${inline(hd[2])}</h${hd[1].length + 2}>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  return out.map(x => Array.isArray(x) ? `<ul>${x.join('')}</ul>` : x).join('');
}
