// "Advanced settings" of the GUI: every parameter of the project's graph as a form (POST /api/settings).
// The server describes the values without the project's own changes (`base`); the changes are kept in the page's
// project document (doc.params, doc.defaults, doc.nodes at the entry's `stores`) and only they are saved.
// A new kind of value = one more renderer in FIELD_KINDS (and a FieldKind of the same name in forms.py).

const Values = {
  isTable: v => v !== null && typeof v === 'object' && !Array.isArray(v),
  same: (a, b) => JSON.stringify(a) === JSON.stringify(b),
  esc: t => String(t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]),
  text(v) {                                   // a value in one short line
    const t = typeof v === 'string' ? v : JSON.stringify(v);
    return t.length > 60 ? t.slice(0, 57) + '...' : t;
  },
  get(root, path) {
    let v = root;
    for (const k of path) { if (!Values.isTable(v) || !(k in v)) return undefined; v = v[k]; }
    return v;
  },
  set(root, path, value) {
    let v = root;
    for (const k of path.slice(0, -1)) { if (!Values.isTable(v[k])) v[k] = {}; v = v[k]; }
    v[path[path.length - 1]] = value;
  },
  remove(root, path) {                        // also removes the tables it leaves empty
    const chain = [root];
    for (const k of path.slice(0, -1)) {
      const next = chain[chain.length - 1][k];
      if (!Values.isTable(next)) return;
      chain.push(next);
    }
    delete chain[chain.length - 1][path[path.length - 1]];
    for (let i = chain.length - 1; i > 0 && !Object.keys(chain[i]).length; i--) delete chain[i - 1][path[i - 1]];
  },
  merged(lower, upper) {                      // like merged_tables: tables key by key, everything else replaced
    if (upper === undefined) return lower;
    if (!Values.isTable(lower) || !Values.isTable(upper)) return upper;
    const out = { ...lower };
    for (const [k, v] of Object.entries(upper)) out[k] = Values.merged(lower[k], v);
    return out;
  },
  leaves: v => Values.isTable(v) ? Object.values(v).reduce((n, x) => n + Values.leaves(x), 0) : 1,
};

// Renderers by field kind: html(field, value) -> the input; read(element, field) -> {value} | {reset} | {error}
function numberKind(whole) {
  const limitText = l => [['minimum', '>='], ['exclusiveMinimum', '>'], ['maximum', '<='], ['exclusiveMaximum', '<']]
    .filter(([k]) => k in l).map(([k, op]) => `${op} ${l[k]}`).join(', ');
  return {
    html: (f, v) => {
      const l = f.limits || {};
      return `<input type="number" step="${whole ? 1 : 'any'}" value="${v ?? ''}"` +
        ('minimum' in l ? ` min="${l.minimum}"` : '') + ('maximum' in l ? ` max="${l.maximum}"` : '') +
        (f.nullable ? ' placeholder="none"' : '') + '>';
    },
    read: (el, f) => {
      if (el.value === '') return f.nullable ? { reset: true } : { error: 'a number is needed' };
      const v = +el.value, l = f.limits || {};
      if (whole && !Number.isInteger(v)) return { error: 'a whole number is needed' };
      if (v < l.minimum || v > l.maximum || v <= l.exclusiveMinimum || v >= l.exclusiveMaximum)
        return { error: `allowed: ${limitText(l)}` };
      return { value: v };
    },
  };
}
const FIELD_KINDS = {
  bool: {
    html: (f, v) => `<input type="checkbox"${v ? ' checked' : ''}>`,
    read: el => ({ value: el.checked }),
  },
  integer: numberKind(true),
  number: numberKind(false),
  text: {
    html: (f, v) => `<input type="text" value="${Values.esc(v ?? '')}">`,
    read: el => ({ value: el.value }),
  },
  choice: {
    html: (f, v) => `<select>${f.choices.map(c => `<option value="${Values.esc(JSON.stringify(c))}"` +
      `${Values.same(c, v) ? ' selected' : ''}>${Values.esc(c)}</option>`).join('')}</select>`,
    read: el => ({ value: JSON.parse(el.value) }),
  },
  list: {                                     // plain values, separated by commas
    html: (f, v) => `<input type="text" value="${Values.esc((v || []).join(', '))}">`,
    read: (el, f) => {
      let v = el.value.split(',').map(x => x.trim()).filter(x => x !== '');
      if (f.item !== 'text') {
        v = v.map(Number);
        if (v.some(x => Number.isNaN(x) || (f.item === 'integer' && !Number.isInteger(x))))
          return { error: f.item === 'integer' ? 'whole numbers, separated by commas' : 'numbers, separated by commas' };
      }
      if (f.length && v.length !== f.length) return { error: `exactly ${f.length} values` };
      return { value: v };
    },
  },
  json: {                                     // everything else, as JSON text
    html: (f, v) => `<textarea rows="${Math.min(8, JSON.stringify(v ?? null, null, 1).split('\n').length)}"` +
      ` spellcheck="false">${Values.esc(JSON.stringify(v ?? null, null, 1))}</textarea>`,
    read: el => {
      try { const v = JSON.parse(el.value); return v === null ? { reset: true } : { value: v }; }
      catch (e) { return { error: 'not valid JSON: ' + e.message }; }
    },
  },
};

class AdvancedSettings {
  // api(url, body): the page's server call; project(): the page's project document (changed in place);
  // page(): the document of the page's values (sent to describe); button: element showing the number of changes
  constructor({ api, project, page, button }) {
    Object.assign(this, { api, project, page, button });
    this.sections = []; this.key = null; this.tab = null; this.error = '';
    this.entries = [];                        // [{entry, section, group}], index = data-e
    this.opened = new Set();                  // open objects: "<entry>:<path>"
    document.body.insertAdjacentHTML('beforeend', `<div id="advModal"><div id="advPanel">
      <div id="advHead"><h2>Advanced settings <span class="info" id="advCount"></span></h2>
        <input type="text" id="advSearch" placeholder="Search (name or description) ...">
        <button id="advReset">Reset this section</button><button id="advClose">Close</button></div>
      <div class="info" id="advNote">Values of the look and the main page; changed values are stored in the
        project (only the changes) - save the project to keep them.</div>
      <div id="advBody"><div id="advNav"></div><div id="advList"></div></div></div></div>`);
    this.el = id => document.getElementById(id);
    this.el('advClose').onclick = () => this.close();
    this.el('advModal').addEventListener('mousedown', e => { if (e.target.id === 'advModal') this.close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && this.isOpen()) this.close(); });
    this.el('advSearch').addEventListener('input', () => this.render());
    this.el('advReset').onclick = () => this.resetSection();
    this.el('advNav').addEventListener('click', e => {
      const tab = e.target.closest('.advTab');
      if (tab) { this.tab = tab.dataset.s; this.el('advSearch').value = ''; this.render(); }
    });
    const list = this.el('advList');
    list.addEventListener('change', e => this.edited(e.target));
    list.addEventListener('click', e => { if (e.target.classList.contains('advUndo')) this.undo(e.target); });
    list.addEventListener('toggle', e => {
      const key = e.target.dataset.open;
      if (key) e.target.open ? this.opened.add(key) : this.opened.delete(key);
    }, true);
    button.onclick = () => this.open();
  }

  // ---------- data ----------
  async refresh() {                           // after loading a project or choosing another look
    const doc = this.page(), key = JSON.stringify([doc.graph, doc.extends]);
    if (key !== this.key) {
      this.key = key;
      try {
        this.sections = (await this.api('/api/settings', { doc })).sections; this.error = '';
      } catch (e) { this.sections = []; this.error = String(e); }
      this.entries = this.sections.flatMap(s => s.groups.flatMap(g => g.entries.map(entry => ({ entry, section: s.key, group: g }))));
      this.opened.clear();
      if (!this.sections.some(s => s.key === this.tab)) this.tab = this.sections.length ? this.sections[0].key : null;
    }
    this.showCounts();
    if (this.isOpen()) this.render();
  }

  change(entry) {                             // the project's change of an entry (undefined: none)
    for (const loc of entry.reads) {
      const v = Values.get(this.project(), loc);
      if (v !== undefined) return v;
    }
    return undefined;
  }

  count(entries) {
    return entries.reduce((n, { entry }) => { const c = this.change(entry); return n + (c === undefined ? 0 : Values.leaves(c)); }, 0);
  }

  write(entry, path, value) {                 // value undefined or equal to the base = no change
    const doc = this.project(), base = Values.get(entry.base, path);
    const isStore = loc => entry.stores.some(s => Values.same(s, loc));
    for (const loc of entry.reads) {          // a change found elsewhere (e.g. [params] instead of [defaults]) moves
      const v = Values.get(doc, loc);
      if (v === undefined || isStore(loc)) continue;
      Values.remove(doc, loc);
      for (const store of entry.stores) Values.set(doc, store, Values.merged(v, Values.get(doc, store)));
    }
    for (const loc of entry.stores) {
      if (value === undefined || Values.same(value, base)) Values.remove(doc, [...loc, ...path]);
      else Values.set(doc, [...loc, ...path], value);
    }
  }

  // ---------- window ----------
  isOpen() { return this.el('advModal').style.display === 'flex'; }
  open() { this.el('advModal').style.display = 'flex'; this.render(); this.el('advSearch').focus(); }
  close() { this.el('advModal').style.display = 'none'; }

  showCounts() {
    const n = this.count(this.entries);
    this.button.textContent = 'Advanced settings ...' + (n ? ` (${n} changed)` : '');
    this.el('advCount').textContent = n ? `${n} changed` : '';
    for (const tab of this.el('advNav').querySelectorAll('.advTab'))
      tab.querySelector('span').textContent = this.count(this.entries.filter(e => e.section === tab.dataset.s)) || '';
    for (const sum of this.el('advList').querySelectorAll('summary[data-e]')) {
      const { entry } = this.entries[sum.dataset.e], c = Values.get(this.change(entry), JSON.parse(sum.dataset.p));
      sum.querySelector('.advCnt').textContent = c === undefined ? '' : `${Values.leaves(c)} changed`;
      sum.querySelector('.advUndo').hidden = c === undefined;
    }
  }

  render() {
    const q = this.el('advSearch').value.trim().toLowerCase();
    this.el('advNav').innerHTML = this.sections.map(s => `<div class="advTab${s.key === this.tab && !q ? ' on' : ''}"` +
      ` data-s="${s.key}">${Values.esc(s.label)} <span></span></div>`).join('');
    let html = this.error ? `<div class="bad">Settings not readable: ${Values.esc(this.error)}</div>` : '';
    for (const section of this.sections) {
      if (!q && section.key !== this.tab) continue;
      let body = '';
      for (const group of section.groups) {
        const hit = !q || `${group.title} ${group.type} ${group.description}`.toLowerCase().includes(q);
        const rows = group.entries.map(entry => this.entryHtml(this.entries.findIndex(e => e.entry === entry), hit ? '' : q))
          .join('');
        if (rows) body += `<div class="advGroup"><h4>${Values.esc(group.title)} <span class="info">${Values.esc(group.type)}` +
          `</span></h4>${group.description ? `<div class="info">${Values.esc(group.description)}</div>` : ''}${rows}</div>`;
      }
      if (body || !q) html += `<h3>${Values.esc(section.label)}</h3>` + (body || '<div class="info">nothing to set</div>');
    }
    this.el('advList').innerHTML = html || '<div class="info">Nothing found.</div>';
    this.el('advReset').disabled = !!q;
    this.showCounts();
  }

  entryHtml(index, q) {
    const { entry } = this.entries[index];
    const hit = !q || `${entry.name} ${entry.description}`.toLowerCase().includes(q);
    return this.nodeHtml(index, entry.field, [], entry.name, entry.description, hit ? '' : q);
  }

  // one value: a row with an input, or an object with its fields (only what the search finds)
  nodeHtml(index, field, path, label, description, q) {
    const { entry } = this.entries[index];
    const change = Values.get(this.change(entry), path), base = Values.get(entry.base, path);
    const key = `${index}:${path.join('.')}`, data = `data-e="${index}" data-p="${Values.esc(JSON.stringify(path))}"`;
    const hit = !q || `${label} ${description}`.toLowerCase().includes(q);
    if (field.kind === 'object') {
      const inner = field.fields.map(f => this.nodeHtml(index, f, [...path, f.key], f.key, f.description || '', hit ? '' : q)).join('');
      if (!inner) return '';
      return `<details class="advObj" data-open="${key}"${this.opened.has(key) || q ? ' open' : ''}>` +
        `<summary ${data}><code>${Values.esc(label)}</code> <span class="advCnt"></span> ` +
        `<span class="info">${Values.esc(description)}</span>` +
        `<button class="advUndo" ${data} title="back to the value of the look">↺</button></summary>${inner}</details>`;
    }
    if (!hit) return '';
    const kind = FIELD_KINDS[field.kind] || FIELD_KINDS.json;
    const value = Values.merged(base, change), unit = field.unit ? ` <span class="info">${Values.esc(field.unit)}</span>` : '';
    return `<div class="advRow${change !== undefined ? ' chg' : ''}" ${data}>` +
      `<div><code>${Values.esc(label)}</code>${unit}<div class="info">look: ${Values.esc(Values.text(base))}</div></div>` +
      `<div class="advIn">${kind.html(field, value)}<div class="advErr"></div></div>` +
      `<div class="advDoc">${Values.esc(description)}</div>` +
      `<div>${change !== undefined ? `<button class="advUndo" ${data} title="back to the value of the look">↺</button>` : ''}</div></div>`;
  }

  fieldAt(index, path) {
    let field = this.entries[index].entry.field;
    for (const k of path) field = field.fields.find(f => f.key === k);
    return field;
  }

  edited(input) {
    const row = input.closest('.advRow');
    if (!row) return;
    const index = +row.dataset.e, path = JSON.parse(row.dataset.p), field = this.fieldAt(index, path);
    const read = (FIELD_KINDS[field.kind] || FIELD_KINDS.json).read(input, field);
    row.querySelector('.advErr').textContent = read.error || '';
    row.classList.toggle('bad', !!read.error);
    if (read.error) return;
    this.write(this.entries[index].entry, path, read.reset ? undefined : read.value);
    this.redraw(row, index, path, field);
  }

  undo(button) {
    const index = +button.dataset.e, path = JSON.parse(button.dataset.p);
    this.write(this.entries[index].entry, path, undefined);
    const node = button.closest('summary') ? button.closest('details') : button.closest('.advRow');
    this.redraw(node, index, path, this.fieldAt(index, path));
  }

  redraw(node, index, path, field) {          // one value again (keeps the focus elsewhere)
    const { entry } = this.entries[index];
    const label = path.length ? path[path.length - 1] : entry.name;
    const description = path.length ? field.description || '' : entry.description;
    node.outerHTML = this.nodeHtml(index, field, path, label, description, '');
    this.showCounts();
  }

  resetSection() {
    if (!confirm('Reset every value of this section to the look?')) return;
    for (const { entry } of this.entries.filter(e => e.section === this.tab))
      for (const loc of [...entry.reads, ...entry.stores]) Values.remove(this.project(), loc);
    this.render();
  }
}
