/* storyteller comments: readers select text, right-click and leave a comment, either a
 * note for other readers or a request for the agent (see comments.py).
 *
 * A comment is anchored by the words it is about, with some text before and after, not
 * by its position, so it finds its place again after the report changes: highlights are
 * made afresh on load and after every live update (runtime.js calls refresh()). One
 * whose words are gone is listed as unanchored.
 *
 * Where comments are saved depends on how the page is open:
 * - in VS Code's interactive tab: through the host (cfHost.saveComments) into the report;
 * - from `storyteller serve`: a POST to /comments, which saves them into the report;
 * - as a file: in this browser until the reader saves a copy with them inside.
 *
 * It runs before runtime.js, so that it can keep a copy of the page as it was built
 * (without drawn charts) to write that copy from.
 */
(function (global) {
  'use strict';

  const document = global.document;
  const slot = document.getElementById('cf-comments');
  const root = document.querySelector('.cf-report');
  if (!slot || !root) return;

  const script = document.currentScript;
  const host = global.cfHost || null;
  const served = !host && !!document.querySelector('meta[name="cf-live"]') && /^https?:$/.test(location.protocol);
  const local = !host && !served;
  // A host without saveComments (an older extension) shows comments but cannot keep new ones.
  const editable = !host || typeof host.saveComments === 'function';
  const pristine = local ? document.documentElement.cloneNode(true) : null;
  const DRAFT_KEY = 'storyteller-comments:' + location.pathname;
  const CONTEXT = 32;
  // Text that is not the report's own: charts, explorers, controls and these comments' UI.
  const SKIP = 'script,style,svg,noscript,.js-plotly-plot,[data-cf-explorer],input,textarea,select,button,.cf-comment-ui';
  const NO_TEXT = new Set(['TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'UL', 'OL', 'DL', 'SELECT']);

  function readSlot() {
    try {
      const value = JSON.parse(slot.textContent || '[]');
      return Array.isArray(value) ? value : [];
    } catch {
      return [];
    }
  }

  const copy = (value) => JSON.parse(JSON.stringify(value));
  let saved = readSlot(); // as in the page (or the report) now
  let exported = JSON.stringify(saved); // a file: what the page or its last saved copy holds
  let comments = copy(saved);
  let located = new Map(); // id -> highlighted?
  let showResolved = false;
  let saveError = null;

  function draft(action, value) {
    if (!local) return null;
    try {
      if (action === 'get') return JSON.parse(global.localStorage.getItem(DRAFT_KEY) || 'null');
      if (value) global.localStorage.setItem(DRAFT_KEY, JSON.stringify(value));
      else global.localStorage.removeItem(DRAFT_KEY);
    } catch {
      /* private windows have no storage: comments last until the page closes */
    }
    return null;
  }

  // Unsaved comments on this file, unless the file changed since they were made.
  const kept = draft('get');
  if (kept && kept.base === exported && Array.isArray(kept.comments)) comments = kept.comments;
  else if (kept) draft('remove');

  // -- the report's text, and points in it -------------------------------------------------

  /** The visible text of `scope`, and where each text node starts in it. */
  function textOf(scope) {
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => (node.parentElement.closest(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT)
    });
    const nodes = [];
    let text = '';
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      nodes.push({ node, start: text.length });
      text += node.data;
    }
    return { text, nodes };
  }

  function offsetOf(model, container, offset) {
    if (container.nodeType === Node.TEXT_NODE) {
      const hit = model.nodes.find((entry) => entry.node === container);
      if (hit) return hit.start + offset;
    }
    const point = document.createRange();
    point.setStart(container, offset);
    for (const entry of model.nodes) if (point.comparePoint(entry.node, 0) >= 0) return entry.start;
    return model.text.length;
  }

  /** What a comment on the selected `range` is anchored by; null outside the report's parts. */
  function anchorOf(range) {
    const common = range.commonAncestorContainer;
    const part = (common.nodeType === 1 ? common : common.parentElement).closest('[data-cf-part]');
    if (!part || ['toc', 'footer'].includes(part.dataset.cfPart)) return null;
    const model = textOf(part);
    let start = offsetOf(model, range.startContainer, range.startOffset);
    let end = offsetOf(model, range.endContainer, range.endOffset);
    while (start < end && /\s/.test(model.text[start])) start++;
    while (end > start && /\s/.test(model.text[end - 1])) end--;
    if (end <= start) return null;
    const first = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    return {
      part: part.dataset.cfPart,
      near: first.closest('[id]')?.id || null,
      exact: model.text.slice(start, end),
      prefix: model.text.slice(Math.max(0, start - CONTEXT), start),
      suffix: model.text.slice(end, end + CONTEXT),
      start
    };
  }

  function shared(a, b, fromEnd) {
    let n = 0;
    while (n < a.length && n < b.length && (fromEnd ? a[a.length - 1 - n] === b[b.length - 1 - n] : a[n] === b[n])) n++;
    return n;
  }

  /** The best match for an anchor: its words, with the most of the text around them unchanged. */
  function locate(anchor) {
    if (!anchor || !anchor.exact) return null;
    const own = anchor.part && root.querySelector(`[data-cf-part="${CSS.escape(anchor.part)}"]`);
    // A renamed or moved section: look everywhere.
    const scopes = own ? [own] : [...root.querySelectorAll('[data-cf-part]:not([data-cf-part="toc"])')];
    let best = null;
    for (const scope of scopes) {
      const model = textOf(scope);
      for (let at = model.text.indexOf(anchor.exact); at >= 0; at = model.text.indexOf(anchor.exact, at + 1)) {
        const end = at + anchor.exact.length;
        const score =
          shared(model.text.slice(Math.max(0, at - CONTEXT), at), anchor.prefix || '', true) +
          shared(model.text.slice(end, end + CONTEXT), anchor.suffix || '', false) -
          Math.abs(at - (anchor.start || 0)) / 1e5;
        if (!best || score > best.score) best = { model, start: at, end, score };
      }
    }
    return best;
  }

  // -- highlights --------------------------------------------------------------------------

  function highlight({ model, start, end }, comment) {
    for (const entry of model.nodes) {
      const from = Math.max(start, entry.start) - entry.start;
      const to = Math.min(end, entry.start + entry.node.data.length) - entry.start;
      if (from >= to || NO_TEXT.has(entry.node.parentNode.nodeName)) continue;
      let node = entry.node;
      if (to < node.data.length) node.splitText(to);
      if (from > 0) node = node.splitText(from);
      const mark = document.createElement('mark');
      mark.className = `cf-mark cf-mark-${comment.kind === 'request' ? 'request' : 'note'}`;
      if (comment.status === 'resolved') mark.classList.add('cf-mark-resolved');
      mark.dataset.cfComment = comment.id;
      node.replaceWith(mark);
      mark.append(node);
    }
  }

  function clearMarks() {
    for (const mark of root.querySelectorAll('mark.cf-mark')) {
      const parent = mark.parentNode;
      mark.replaceWith(...mark.childNodes);
      parent.normalize();
    }
  }

  function apply() {
    clearMarks();
    located = new Map();
    for (const comment of comments) {
      const hit = locate(comment.anchor);
      located.set(comment.id, !!hit);
      if (hit && (showResolved || comment.status !== 'resolved')) highlight(hit, comment);
    }
    renderBar();
    if (!panel.hidden) renderPanel();
  }

  // -- saving --------------------------------------------------------------------------------

  const dirty = () => JSON.stringify(comments) !== exported;

  function persist() {
    const list = copy(comments);
    saveError = null;
    if (host) {
      host.saveComments(JSON.stringify(list));
      saved = list;
    } else if (served) {
      fetch('/comments', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(list) })
        .then((response) => {
          if (!response.ok) throw new Error(response.statusText || String(response.status));
          saved = list;
        })
        .catch((error) => (saveError = error.message))
        .finally(renderBar);
    } else {
      draft('set', dirty() ? { base: exported, comments: list } : null);
    }
    renderBar();
  }

  /** The page as it was built, with the current comments in it. */
  function serialize() {
    const page = pristine.cloneNode(true);
    // The scripts after this one were not parsed yet when the copy was taken.
    let after = page.querySelector('#cf-comments').nextElementSibling;
    for (let node = script.nextSibling; node; node = node.nextSibling) {
      if (node.nodeName !== 'SCRIPT') continue;
      const next = node.cloneNode(true);
      after.after(next);
      after = next;
    }
    page.querySelector('#cf-comments').textContent = JSON.stringify(comments).replace(/</g, '\\u003c');
    return '<!doctype html>\n' + page.outerHTML;
  }

  async function saveCopy() {
    const html = serialize();
    const name = decodeURIComponent(location.pathname.split('/').pop() || 'report.html');
    let done = false;
    // Chromium can write over the file itself, if the reader picks it; others download.
    if (global.showSaveFilePicker) {
      try {
        const handle = await global.showSaveFilePicker({
          suggestedName: name,
          types: [{ description: 'HTML report', accept: { 'text/html': ['.html'] } }]
        });
        const writable = await handle.createWritable();
        await writable.write(html);
        await writable.close();
        done = true;
      } catch (error) {
        if (error.name === 'AbortError') return;
      }
    }
    if (!done) {
      const link = document.createElement('a');
      link.href = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    }
    // The draft stays: the copy may have gone elsewhere than this file.
    exported = JSON.stringify(comments);
    renderBar();
  }

  // -- the comments' UI ------------------------------------------------------------------------

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(label, action, primary) {
    const node = element('button', primary ? 'cf-button cf-button-primary' : 'cf-button', label);
    node.type = 'button';
    node.dataset.do = action;
    return node;
  }

  function popup(className, label) {
    const node = element('div', `cf-comment-ui ${className}`);
    node.hidden = true;
    node.setAttribute('role', 'dialog');
    node.setAttribute('aria-label', label);
    root.append(node);
    return node;
  }

  const menu = popup('cf-comment-menu', 'Comment');
  menu.setAttribute('role', 'menu');
  for (const [label, kind] of [
    ['Comment', 'note'],
    ['Ask the agent…', 'request']
  ]) {
    const item = button(label, kind);
    item.className = 'cf-comment-menu-item';
    item.setAttribute('role', 'menuitem');
    menu.append(item);
  }

  const editor = popup('cf-comment-editor', 'Write a comment');
  const textarea = element('textarea');
  textarea.rows = 4;
  textarea.placeholder = 'Your comment';
  const forAgent = element('input');
  forAgent.type = 'checkbox';
  const agentLabel = element('label', 'cf-comment-kind');
  agentLabel.append(forAgent, ' For the agent: a change or a question');
  const editorActions = element('div', 'cf-comment-actions');
  editorActions.append(button('Cancel', 'cancel'), button('Save', 'save', true));
  editor.append(textarea, agentLabel, editorActions);

  const card = popup('cf-comment-card', 'Comment');
  const panel = popup('cf-comments-panel', 'Comments');
  const bar = element('div', 'cf-comment-ui cf-comments-bar');
  root.append(bar);

  let pending = null; // { anchor } for a new comment, { id } when editing one
  let cardFor = null;

  function place(node, x, y) {
    node.hidden = false;
    node.style.left = `${Math.max(8, Math.min(x, global.innerWidth - node.offsetWidth - 8))}px`;
    node.style.top = `${Math.max(8, Math.min(y, global.innerHeight - node.offsetHeight - 8))}px`;
  }

  function closePopups() {
    menu.hidden = editor.hidden = card.hidden = true;
  }

  function openEditor(x, y, body, kind) {
    textarea.value = body;
    forAgent.checked = kind === 'request';
    place(editor, x, y);
    textarea.focus();
  }

  function saveEditor() {
    const body = textarea.value.trim();
    const kind = forAgent.checked ? 'request' : 'note';
    editor.hidden = true;
    if (!body || !pending) return;
    const now = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    if (pending.id) {
      const comment = comments.find((c) => c.id === pending.id);
      if (comment) Object.assign(comment, { body, kind, updated: now });
    } else {
      const id = 'c-' + Math.random().toString(36).slice(2, 10).padEnd(8, '0');
      comments.push({ id, kind, status: 'open', body, anchor: pending.anchor, created: now });
    }
    pending = null;
    global.getSelection()?.removeAllRanges();
    apply();
    persist();
  }

  function kindLabel(comment) {
    return (comment.kind === 'request' ? 'For the agent' : 'Note') + (comment.status === 'resolved' ? ' · resolved' : '');
  }

  function showCard(comment, box) {
    cardFor = comment.id;
    card.replaceChildren(element('p', 'cf-comment-meta', kindLabel(comment)), element('p', 'cf-comment-body', comment.body));
    for (const reply of comment.replies || []) {
      const line = element('p', 'cf-comment-reply');
      line.append(element('strong', '', `${reply.author || 'agent'}: `), reply.body);
      card.append(line);
    }
    if (editable) {
      const actions = element('div', 'cf-comment-actions');
      actions.append(
        button('Edit', 'edit'),
        button(comment.status === 'resolved' ? 'Reopen' : 'Resolve', 'toggle'),
        button('Delete', 'delete')
      );
      card.append(actions);
    }
    place(card, box.left, box.bottom + 6);
  }

  function act(action, comment, x, y) {
    if (action === 'delete') comments = comments.filter((c) => c !== comment);
    else if (action === 'toggle') {
      comment.status = comment.status === 'resolved' ? 'open' : 'resolved';
      comment.updated = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    } else if (action === 'edit') {
      pending = { id: comment.id };
      openEditor(x, y, comment.body, comment.kind);
      return;
    } else return;
    apply();
    persist();
  }

  function reveal(comment) {
    if (comment.status === 'resolved' && !showResolved) {
      showResolved = true;
      apply();
    }
    const mark = root.querySelector(`mark[data-cf-comment="${CSS.escape(comment.id)}"]`);
    if (!mark) return;
    mark.closest('details:not([open])')?.setAttribute('open', '');
    mark.scrollIntoView({ block: 'center', behavior: 'smooth' });
    for (const each of root.querySelectorAll(`mark[data-cf-comment="${CSS.escape(comment.id)}"]`)) {
      each.classList.add('cf-mark-flash');
      setTimeout(() => each.classList.remove('cf-mark-flash'), 1600);
    }
  }

  function quote(text, length = 120) {
    const flat = (text || '').replace(/\s+/g, ' ').trim();
    return flat.length > length ? flat.slice(0, length - 1) + '…' : flat;
  }

  function renderPanel() {
    const header = element('div', 'cf-comments-panel-head');
    const toggle = element('label', 'cf-comment-kind');
    const box = element('input');
    box.type = 'checkbox';
    box.checked = showResolved;
    box.dataset.do = 'resolved';
    toggle.append(box, ' Show resolved');
    header.append(element('strong', '', 'Comments'), toggle, button('Close', 'close'));
    const list = element('ol', 'cf-comments-list');
    for (const comment of comments) {
      if (comment.status === 'resolved' && !showResolved) continue;
      const item = element('li', `cf-comments-item cf-comments-item-${comment.kind === 'request' ? 'request' : 'note'}`);
      item.dataset.cfComment = comment.id;
      item.append(element('p', 'cf-comment-meta', kindLabel(comment)));
      item.append(element('p', 'cf-comment-quote', `“${quote(comment.anchor?.exact)}”`));
      item.append(element('p', 'cf-comment-body', comment.body));
      if (!located.get(comment.id)) {
        item.classList.add('cf-comments-item-lost');
        item.append(element('p', 'cf-comment-meta', 'These words are no longer in the report.'));
        if (editable) item.append(button('Delete', 'delete'));
      }
      list.append(item);
    }
    if (!list.children.length) list.append(element('li', 'cf-comment-meta', 'No open comments.'));
    panel.replaceChildren(header, list);
  }

  function requestsText() {
    const open = comments.filter((c) => c.kind === 'request' && c.status === 'open');
    const lines = [
      `I left ${open.length === 1 ? 'a comment' : `${open.length} comments`} for you in the report. ` +
        'When you have dealt with one, resolve it with `storyteller comments resolve REPORT ID "what you did"` ' +
        '(`storyteller comments list REPORT` shows them all).',
      ''
    ];
    for (const comment of open) {
      lines.push(`- ${comment.id} in ${comment.anchor?.part || 'the report'}, on "${quote(comment.anchor?.exact, 200)}":`);
      lines.push(`  ${comment.body.replace(/\n/g, '\n  ')}`);
    }
    return lines.join('\n');
  }

  function renderBar() {
    bar.replaceChildren();
    bar.hidden = !comments.length;
    if (!comments.length) return;
    const open = comments.filter((c) => c.status === 'open').length;
    const lost = comments.filter((c) => located.get(c.id) === false && c.status === 'open').length;
    const count = button(`${open} comment${open === 1 ? '' : 's'}`, 'panel');
    count.title = 'List the comments';
    bar.append(count);
    if (lost) bar.append(element('span', 'cf-comments-lost', `${lost} unanchored`));
    if (saveError) bar.append(element('span', 'cf-comments-lost', `Not saved: ${saveError}`));
    if (host && editable && typeof host.send === 'function') {
      const requests = comments.filter((c) => c.kind === 'request' && c.status === 'open').length;
      if (requests) {
        const send = button('Send to agent', 'send', true);
        send.title = 'Put the open requests in the feedback box, to read before you send';
        bar.append(send);
      }
    }
    if (local) {
      if (dirty()) bar.append(element('span', 'cf-comments-unsaved', 'Not in the file yet'));
      const save = button('Save copy', 'save-copy', dirty());
      save.title = 'Save the report with its comments inside, to keep or share';
      bar.append(save);
    }
  }

  // -- events ---------------------------------------------------------------------------------

  if (editable) {
    root.addEventListener('contextmenu', (event) => {
      const selection = global.getSelection();
      if (!selection || selection.isCollapsed || !selection.rangeCount) return;
      if (event.target.closest?.('.cf-comment-ui')) return;
      const range = selection.getRangeAt(0);
      const anchor = anchorOf(range);
      if (!anchor) return;
      event.preventDefault();
      closePopups();
      pending = { anchor, box: range.getBoundingClientRect() };
      place(menu, event.clientX, event.clientY);
      menu.querySelector('button').focus();
    });
  }

  menu.addEventListener('click', (event) => {
    const kind = event.target.closest('[data-do]')?.dataset.do;
    if (!kind) return;
    menu.hidden = true;
    // Below the words, so they stay in sight while writing.
    const box = pending?.box;
    openEditor(box ? box.left : parseFloat(menu.style.left), box ? box.bottom + 6 : parseFloat(menu.style.top), '', kind);
  });

  editor.addEventListener('click', (event) => {
    const action = event.target.closest('[data-do]')?.dataset.do;
    if (action === 'cancel') {
      editor.hidden = true;
      pending = null;
    } else if (action === 'save') saveEditor();
  });
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) saveEditor();
  });

  root.addEventListener('mouseover', (event) => {
    const mark = event.target.closest?.('mark.cf-mark');
    if (!mark || !editor.hidden) return;
    const comment = comments.find((c) => c.id === mark.dataset.cfComment);
    if (comment && (cardFor !== comment.id || card.hidden)) showCard(comment, mark.getBoundingClientRect());
  });
  document.addEventListener('mouseover', (event) => {
    if (!card.hidden && !event.target.closest?.('mark.cf-mark, .cf-comment-card')) {
      card.hidden = true;
      cardFor = null;
    }
  });
  card.addEventListener('click', (event) => {
    const action = event.target.closest('[data-do]')?.dataset.do;
    const comment = comments.find((c) => c.id === cardFor);
    if (!comment || !action) return;
    card.hidden = true;
    cardFor = null;
    act(action, comment, parseFloat(card.style.left), parseFloat(card.style.top));
  });

  panel.addEventListener('click', (event) => {
    const action = event.target.closest('[data-do]')?.dataset.do;
    if (action === 'close') {
      panel.hidden = true;
      return;
    }
    if (action === 'resolved') {
      showResolved = event.target.checked;
      apply();
      return;
    }
    const item = event.target.closest('[data-cf-comment]');
    const comment = item && comments.find((c) => c.id === item.dataset.cfComment);
    if (!comment) return;
    if (action === 'delete') act('delete', comment);
    else reveal(comment);
  });

  bar.addEventListener('click', (event) => {
    const action = event.target.closest('[data-do]')?.dataset.do;
    if (action === 'panel') {
      if (panel.hidden) {
        renderPanel();
        panel.hidden = false;
      } else panel.hidden = true;
    } else if (action === 'save-copy') saveCopy();
    else if (action === 'send') host.send(requestsText());
  });

  document.addEventListener('mousedown', (event) => {
    if (!menu.hidden && !event.target.closest?.('.cf-comment-menu')) menu.hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!menu.hidden || !editor.hidden || !card.hidden) closePopups();
    else panel.hidden = true;
  });
  global.addEventListener('scroll', () => (card.hidden = true), { passive: true });

  /** After a live update: the comments the report has now, unless this page has unsaved ones. */
  function refresh() {
    const incoming = readSlot();
    if (JSON.stringify(incoming) !== JSON.stringify(saved)) {
      if (!(local && dirty())) comments = copy(incoming);
      saved = incoming;
      if (local) exported = JSON.stringify(incoming);
    }
    apply();
  }

  // The runtime comes after this script and draws into the page: anchor once it has.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
  else apply();
  global.cfComments = { list: () => copy(comments), refresh, serialize: () => (pristine ? serialize() : null), saveCopy };
})(typeof window !== 'undefined' ? window : globalThis);
