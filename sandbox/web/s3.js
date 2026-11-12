/* Amazon S3 console: browse what the DAG unloaded. */
App.apps.s3 = (() => {
  let root, prefix = '', objects = [], viewing = null;

  async function load() {
    const res = await api('/api/s3');
    objects = res.objects || [];
    render();
  }

  function render() {
    const crumbs = ['Amazon S3', 'Buckets', 'atlas-exports', ...prefix.split('/').filter(Boolean)];
    root.replaceChildren(
      h('header.s3-top', h('span.s3-logo', 'aws'), h('span.s3-services', '▦ Services'), h('input.s3-search', { placeholder: 'Search  [Alt+S]' }),
        h('span.s3-right', 'United States (Ohio) ▾   atlas-dev ▾')),
      h('div.s3-body',
        h('div.s3-crumbs', crumbs.map((c, i) => [i ? h('span.s3-sep', '›') : null,
          h(i < crumbs.length - 1 ? 'a' : 'span', {
            onclick: () => {
              if (i < 3) { prefix = ''; viewing = null; render(); return; }
              prefix = crumbs.slice(3, i + 1).join('/') + '/'; viewing = null; render();
            },
          }, c)])),
        viewing ? objectPage() : listing(),
      ),
    );
  }

  function listing() {
    const seen = new Map();
    for (const o of objects) {
      if (!o.key.startsWith(prefix)) continue;
      const rest = o.key.slice(prefix.length);
      const slash = rest.indexOf('/');
      if (slash >= 0) {
        const name = rest.slice(0, slash + 1);
        if (!seen.has(name)) seen.set(name, { folder: true, name });
      } else seen.set(rest, { ...o, name: rest });
    }
    const items = [...seen.values()];
    return h('div.s3-card',
      h('div.s3-card-h', h('h2', prefix ? prefix.split('/').filter(Boolean).pop() + '/' : 'atlas-exports', h('span.s3-info', ' Info')),
        h('div', h('button.s3-btn', { onclick: load }, '↻'), h('button.s3-btn', { disabled: true }, 'Copy S3 URI'), h('button.s3-btn', { disabled: true }, 'Download'),
          h('button.s3-btn', { disabled: true }, 'Open'), h('button.s3-btn', { disabled: true }, 'Delete'), h('button.s3-btn.orange', { disabled: true }, 'Upload'))),
      h('div.s3-tabs', h('span.on', 'Objects'), h('span', 'Properties'), h('span', 'Permissions'), h('span', 'Metrics'), h('span', 'Management')),
      h('p.s3-desc', 'Objects are the fundamental entities stored in Amazon S3. The delta sync DAG unloads each EXPORT_VIEWS view here; a downstream team loads them into the target system.'),
      h('table.s3-table',
        h('thead', h('tr', h('th', h('input', { type: 'checkbox' })), ['Name', 'Type', 'Last modified', 'Size', 'Storage class'].map(x => h('th', x)))),
        h('tbody', items.length ? items.map(it => h('tr',
          h('td', h('input', { type: 'checkbox' })),
          h('td', h('a', { onclick: () => { if (it.folder) prefix += it.name; else viewing = it; render(); } }, it.folder ? '📁 ' : '📄 ', it.name)),
          h('td', it.folder ? 'Folder' : 'csv'),
          h('td', it.folder ? '-' : it.modified),
          h('td', it.folder ? '-' : `${(it.size / 1024).toFixed(1)} KB`),
          h('td', it.folder ? '-' : 'Standard'),
        )) : h('tr', h('td.s3-empty', { colspan: 6 }, 'No objects. Trigger the delta sync DAG in Airflow to export the views here.'))),
      ),
    );
  }

  function objectPage() {
    const body = h('div.s3-preview', spinner('Loading object...'));
    api(`/api/s3/object?key=${encodeURIComponent(viewing.key)}`).then(res => {
      const lines = (res.text || '').trim().split('\n').filter(Boolean);
      const parse = line => {
        const out = []; let cur = '', q = false;
        for (let i = 0; i < line.length; i++) {
          const c = line[i];
          if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
          else if (c === '"') q = true; else if (c === ',') { out.push(cur); cur = ''; } else cur += c;
        }
        out.push(cur);
        return out;
      };
      const header = lines.length ? parse(lines[0]) : [];
      const rows = lines.slice(1).map(l => parse(l).map(v => (v === '' ? null : v)));
      body.replaceChildren(
        h('div.s3-sub', `S3 Select preview · ${rows.length} record${rows.length === 1 ? '' : 's'}`),
        rows.length ? renderGrid(header.map(n => ({ name: n.toUpperCase(), type: 'VARCHAR' })), rows) : h('div.muted.pad', 'Header only: the view returned no rows for this run.'));
    });
    return h('div.s3-card',
      h('div.s3-card-h', h('h2', viewing.name), h('div', h('button.s3-btn', { onclick: () => { viewing = null; render(); } }, 'Back'))),
      h('div.s3-overview',
        h('div', h('small', 'S3 URI'), h('code', `s3://atlas-exports/${viewing.key}`)),
        h('div', h('small', 'Last modified'), viewing.modified),
        h('div', h('small', 'Size'), `${viewing.size} B`)),
      body,
    );
  }

  return {
    mount(el) { root = el; root.classList.add('s3'); load(); },
    show: load,
    refresh: load,
  };
})();
