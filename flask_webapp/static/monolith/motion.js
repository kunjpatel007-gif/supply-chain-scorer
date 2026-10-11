/* Monolith motion layer (decorative only: if this file fails, every page still works). */
(() => {
  const d = document, root = d.documentElement;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)').matches;
  const raf = fn => { let q = false, a; return (...args) => { a = args; if (!q) { q = true; requestAnimationFrame(() => { q = false; fn(...a); }); } }; };

  /* Rolling labels: split text into letters that roll up on hover (nav, brand, log out, buttons). */
  const roll = el => {
    if (el.dataset.rolled || el.children.length) return;
    const text = el.textContent.trim(); if (!text) return;
    el.dataset.rolled = '1'; el.setAttribute('aria-label', text);
    const wrap = d.createElement('span'); wrap.className = 'roll'; wrap.setAttribute('aria-hidden', 'true');
    [...text].forEach((ch, i) => { const s = d.createElement('span'); s.textContent = ch; s.dataset.c = ch; s.style.setProperty('--i', i); wrap.appendChild(s); });
    el.textContent = ''; el.appendChild(wrap);
  };
  d.querySelectorAll('.bar nav a, .bar .lo, .bar .brand, .btn').forEach(roll);

  /* Gliding nav indicator: sits under the current page, follows the hovered tab. */
  const nav = d.querySelector('.bar nav');
  if (nav) {
    const ind = d.createElement('span'); ind.className = 'nav-ind instant'; nav.appendChild(ind);
    const current = nav.querySelector('[aria-current=page]');
    const place = a => {
      if (!a) { ind.style.opacity = 0; return; }
      const n = nav.getBoundingClientRect(), r = a.getBoundingClientRect();
      ind.style.opacity = 1; ind.style.setProperty('--x', (r.left - n.left) + 'px'); ind.style.setProperty('--w', r.width);
    };
    place(current); requestAnimationFrame(() => requestAnimationFrame(() => ind.classList.remove('instant')));
    if (finePointer) {
      nav.querySelectorAll('a').forEach(a => a.addEventListener('pointerenter', () => place(a)));
      nav.addEventListener('pointerleave', () => place(current));
    }
    addEventListener('resize', raf(() => place(current)));
  }

  /* Headline words rise out of a mask; on .chars headlines every letter does (sign-in page).
     Skipped for titles that morph in from the previous page. */
  if (!reduce) d.querySelectorAll('h1.display').forEach(h => {
    if (h.classList.contains('vt-title')) return;
    const chars = h.classList.contains('chars');
    let i = 0;
    const piece = (frag, text) => {
      const w = d.createElement('span'), inner = d.createElement('span');
      w.className = 'w'; inner.textContent = text; inner.style.setProperty('--i', i++); w.appendChild(inner); frag.appendChild(w);
    };
    const walk = node => [...node.childNodes].forEach(n => {
      if (n.nodeType === 3) {
        const frag = d.createDocumentFragment();
        n.textContent.split(/(\s+)/).forEach(part => {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(d.createTextNode(part)); return; }
          if (!chars) { piece(frag, part); return; }
          const word = d.createElement('span'); word.style.whiteSpace = 'nowrap';       // letters never break mid-word
          [...part].forEach(ch => piece(word, ch)); frag.appendChild(word);
        });
        n.replaceWith(frag);
      } else if (n.nodeType === 1 && n.tagName !== 'BR') walk(n);
    });
    if (chars) h.setAttribute('aria-label', h.textContent.replace(/\s+/g, ' ').trim());
    walk(h);
  });

  if (finePointer && !reduce) {
    /* Cursor light that follows the pointer along a row. */
    const light = raf((row, x) => row.style.setProperty('--mx', x + 'px'));
    d.addEventListener('pointermove', e => {
      const row = e.target.closest && e.target.closest('tr.link, .lrow');
      if (row) light(row, e.clientX - row.getBoundingClientRect().left);
    }, { passive: true });

    /* Magnetic buttons: drift up to 4px toward the cursor. */
    d.querySelectorAll('.btn').forEach(b => {
      const move = raf(e => {
        const r = b.getBoundingClientRect();
        const x = ((e.clientX - r.left) / r.width - .5) * 8, y = ((e.clientY - r.top) / r.height - .5) * 6;
        b.style.translate = `${x.toFixed(1)}px ${y.toFixed(1)}px`;
      });
      b.addEventListener('pointermove', move);
      b.addEventListener('pointerleave', () => { b.style.translate = ''; });
    });

    /* Cursor ring: trails the pointer, grows over links, shrinks to a dot on rows (in the row's
       band colour), and wraps buttons like a frame. Runs only while it still has distance to cover. */
    const ring = d.createElement('div'); ring.className = 'cursor'; ring.setAttribute('aria-hidden', 'true'); d.body.appendChild(ring);
    let x = 0, y = 0, tx = 0, ty = 0, loop = 0, target = null, seen = false;
    const frame = () => {
      let gx = tx, gy = ty;
      if (target) { const r = target.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2; gx = cx + (tx - cx) * .1; gy = cy + (ty - cy) * .1; }
      x += (gx - x) * .24; y += (gy - y) * .24;
      ring.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0)`;
      loop = (target || Math.abs(gx - x) > .25 || Math.abs(gy - y) > .25) ? requestAnimationFrame(frame) : 0;
    };
    addEventListener('pointermove', e => {
      if (e.pointerType !== 'mouse') return;
      tx = e.clientX; ty = e.clientY;
      if (!seen) { seen = true; x = tx; y = ty; ring.classList.add('on'); }
      if (!loop) loop = requestAnimationFrame(frame);
    }, { passive: true });
    d.addEventListener('mouseout', e => { if (!e.relatedTarget) { ring.classList.remove('on'); seen = false; } });
    d.addEventListener('pointerdown', () => ring.classList.add('down'));
    d.addEventListener('pointerup', () => ring.classList.remove('down'));
    d.addEventListener('pointerover', e => {
      const el = e.target.closest ? e.target : null;
      let mode = '', on = null;
      if (!el) mode = '';
      else if (el.closest('input[type=text],input[type=password],input[type=number],input[type=datetime-local],textarea')) mode = 'text';
      else if ((on = el.closest('.btn'))) mode = 'btn';
      else if ((on = el.closest('tr.link,.lrow')) && !el.closest('a.fk')) mode = 'row';
      else if (el.closest('a,button,label,[role=option],.hist .b')) mode = 'link';
      ring.dataset.mode = mode;
      target = mode === 'btn' ? on : null;
      if (mode === 'btn') {
        const r = on.getBoundingClientRect();
        ring.style.setProperty('--cw', (r.width + 14) + 'px'); ring.style.setProperty('--ch', (r.height + 14) + 'px');
        if (!loop) loop = requestAnimationFrame(frame);
      } else { ring.style.removeProperty('--cw'); ring.style.removeProperty('--ch'); }
      if (mode === 'row') ring.style.setProperty('--c', getComputedStyle(on).getPropertyValue('--c').trim() || 'var(--sand)');
    });
  }

  /* Overview: hovering a band count highlights its bars; hovering a bar underlines its count and
     lights its neighbours like a lens. Counts re-spin like a counter when hovered. */
  const hist = d.querySelector('.hist');
  if (hist && finePointer) {
    const stats = [...d.querySelectorAll('a.stat')], bars = [...hist.querySelectorAll('.b')];
    const band = el => ['High', 'Medium', 'Low'].find(c => el.classList.contains(c));
    let near = [];
    stats.forEach(s => {
      s.addEventListener('pointerenter', () => { hist.dataset.focus = band(s); });
      s.addEventListener('pointerleave', () => { delete hist.dataset.focus; });
    });
    hist.addEventListener('pointerover', e => {
      const b = e.target.closest('.b'); if (!b) return;
      hist.dataset.bar = '1'; const c = band(b), i = bars.indexOf(b);
      stats.forEach(s => s.classList.toggle('lit', band(s) === c));
      near.forEach(x => delete x.dataset.near); near = [];
      for (let k = 1; k <= 3; k++) [bars[i - k], bars[i + k]].forEach(x => { if (x) { x.dataset.near = k; near.push(x); } });
    });
    hist.addEventListener('pointerleave', () => {
      delete hist.dataset.bar; stats.forEach(s => s.classList.remove('lit'));
      near.forEach(x => delete x.dataset.near); near = [];
    });
  }
  if (finePointer && !reduce) d.querySelectorAll('a.stat .n[data-count]').forEach(n =>
    n.closest('a.stat').addEventListener('pointerenter', () => window.odometer && odometer(n, null, true)));

  /* Pause hover effects while scrolling (rows sliding under a still pointer would repaint every frame). */
  let st = 0;
  addEventListener('scroll', () => {
    if (!st) root.classList.add('is-scrolling');
    clearTimeout(st); st = setTimeout(() => { root.classList.remove('is-scrolling'); st = 0; }, 150);
  }, { passive: true });

  /* Section rules draw themselves the first time they come into view. */
  if (!reduce && 'IntersectionObserver' in window) {
    const seen = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add('drawn'); seen.unobserve(e.target); }
    }), { rootMargin: '0px 0px -6% 0px' });
    d.querySelectorAll('.head:not(.bare), .sec').forEach(s => {
      const h = d.createElement('i'); h.className = 'hair'; h.setAttribute('aria-hidden', 'true'); s.appendChild(h); seen.observe(h);
    });
    root.classList.add('hairs');
  }

  /* Reading position on long pages (drawn by a scroll-driven animation, no script per frame). */
  if (!reduce) { const line = d.createElement('div'); line.className = 'scroll-line'; line.setAttribute('aria-hidden', 'true'); d.body.appendChild(line); }

  /* Flash messages: a fading life-line, then they settle into a quieter tone. */
  d.querySelectorAll('.flash').forEach(f => f.addEventListener('animationend', e => {
    if (e.pseudoElement === '::after') f.classList.add('settled');
  }));

  /* Segmented choices: an ink pill slides between options. */
  d.querySelectorAll('.seg:not(.quick)').forEach(seg => {
    const ind = d.createElement('span'); ind.className = 'seg-ind instant'; ind.setAttribute('aria-hidden', 'true');
    seg.prepend(ind); seg.classList.add('js');
    const place = () => {
      const on = seg.querySelector('input:checked'), lab = on && on.closest('label'), span = lab && lab.querySelector('span');
      if (!span) { ind.style.opacity = 0; return; }
      ind.style.opacity = 1;
      ind.style.transform = `translate(${lab.offsetLeft}px,${lab.offsetTop}px)`;
      ind.style.width = span.offsetWidth + 'px'; ind.style.height = span.offsetHeight + 'px';
    };
    place(); requestAnimationFrame(() => requestAnimationFrame(() => ind.classList.remove('instant')));
    seg.addEventListener('change', place);
    addEventListener('resize', raf(place));
  });

  /* Hold to delete: press and hold; the fill tracks the press and each row of the cascade preview is
     struck through in turn. Letting go early cancels. A plain click never deletes. */
  d.querySelectorAll('[data-hold]').forEach(btn => {
    const form = btn.form, lbl = btn.querySelector('.lbl'), tree = d.querySelector('.tree'), HOLD = 1200;
    if (!form) return;
    if (lbl) lbl.textContent = 'Hold to ' + lbl.textContent.charAt(0).toLowerCase() + lbl.textContent.slice(1);
    let t0 = 0, id = 0, done = false;
    const set = p => { btn.style.setProperty('--p', p.toFixed(3)); if (tree) tree.style.setProperty('--p', p.toFixed(3)); btn.classList.toggle('late', p > .72); };
    const tick = now => {
      const p = Math.min(1, (now - t0) / HOLD); set(p);
      if (p < 1) { id = requestAnimationFrame(tick); return; }
      done = true; btn.classList.remove('holding', 'late');
      if (lbl) lbl.textContent = 'Deleting…';
      form.requestSubmit ? form.requestSubmit() : form.submit();
    };
    const start = e => {
      if (done || btn.classList.contains('holding') || (e.button != null && e.button !== 0)) return;
      e.preventDefault(); t0 = performance.now(); btn.classList.add('holding'); id = requestAnimationFrame(tick);
    };
    const stop = () => {
      if (done || !btn.classList.contains('holding')) return;
      cancelAnimationFrame(id); btn.classList.remove('holding', 'late'); set(0);
    };
    btn.addEventListener('pointerdown', start);
    ['pointerup', 'pointerleave', 'pointercancel', 'blur'].forEach(ev => btn.addEventListener(ev, stop));
    btn.addEventListener('keydown', e => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) start(e); });
    btn.addEventListener('keyup', e => { if (e.key === ' ' || e.key === 'Enter') stop(); });
    btn.addEventListener('click', e => { if (!done) e.preventDefault(); });
  });

  /* Busy state on submit (e.g. simulator re-scoring). */
  d.addEventListener('submit', e => {
    const btn = e.target.querySelector('button[type=submit]');
    if (btn && e.target.checkValidity()) btn.classList.add('is-busy');
  });

  /* Progress line for slow navigations (cold starts, big pages). Shown only if loading takes >150ms. */
  const bar = d.createElement('div'); bar.className = 'nav-progress'; d.body.appendChild(bar);
  let pend = 0;
  const pending = () => { clearTimeout(pend); pend = setTimeout(() => root.classList.add('nav-pending'), 150); };
  d.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target || a.hasAttribute('download')) return;
    const u = new URL(a.href, location.href);
    if (u.origin === location.origin && u.href.split('#')[0] !== location.href.split('#')[0]) pending();
  });
  d.addEventListener('submit', pending);
  addEventListener('pageshow', () => { clearTimeout(pend); root.classList.remove('nav-pending', 'is-busy'); d.querySelectorAll('.is-busy').forEach(b => b.classList.remove('is-busy')); });

  /* Overview "Highest risk": compare with the list this browser saw last time. Rows that moved
     start at their old place and slide to the new one after the wipe; ▲N / ▼N badges say how far. */
  const top = [...d.querySelectorAll('tr[data-top]')];
  if (top.length) {
    let seen = null;
    try { seen = JSON.parse(localStorage.getItem('nx-top') || 'null'); } catch (e) {}
    try { localStorage.setItem('nx-top', JSON.stringify(Object.fromEntries(top.map((r, i) => [r.dataset.top, [i, +r.dataset.score]])))); } catch (e) {}
    if (seen) {
      const h = top[0].getBoundingClientRect().height, slides = [];
      top.forEach((r, i) => {
        const was = seen[r.dataset.top], sc = r.querySelector('.sc');
        let txt = '', cls = '';
        if (!was) { txt = 'NEW'; cls = 'new'; }
        else if (was[0] !== i) { const n = was[0] - i; txt = (n > 0 ? '▲' : '▼') + Math.abs(n); cls = n > 0 ? 'up' : 'down'; }
        else if (Math.abs(was[1] - r.dataset.score) >= 0.05) { const n = r.dataset.score - was[1]; txt = (n > 0 ? '+' : '') + n.toFixed(1); cls = 'same'; }
        if (!txt) return;
        r.classList.add('moved-row');
        const b = d.createElement('span'); b.className = 'rkd ' + cls; b.textContent = txt;
        b.style.setProperty('--dl', reduce ? '0s' : (1.5 + i * 0.05) + 's');
        sc.prepend(b);
        if (!reduce) {
          const dy = was ? (was[0] - i) * h : (top.length - i) * h;   // new rows come up from below the list
          if (dy) { r.style.translate = `0 ${dy}px`; slides.push([r, dy, !was]); }
        }
      });
      if (slides.length) setTimeout(() => slides.forEach(([r, dy, fresh], k) => {
        r.style.translate = '';
        r.animate([{ translate: `0 ${dy}px`, opacity: fresh ? 0 : 1 }, { translate: '0 0', opacity: 1 }],
                  { duration: 800, delay: k * 40, easing: 'cubic-bezier(.22,1,.36,1)' });
      }), 1300);
    }
  }
})();
