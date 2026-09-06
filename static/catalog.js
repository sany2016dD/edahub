// Парсер каталога Яндекса Еды: адрес → магазины → категории/товары → скидки.
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = s => {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  };
  const fmt = n => (n == null ? '' : String(n).replace('.', ','));
  const PAGE = 60;
  const state = { sort: 'discount', cat: '', discount: false };
  let storeIdActive = null;
  let page = 1;
  let total = 0;

  async function api(url, opts) {
    const r = await fetch(url, opts);
    let data;
    try { data = await r.json(); }
    catch (e) { data = null; }
    if (!r.ok || !data || data.ok === false) {
      throw new Error((data && (data.error || data.message)) || ('HTTP ' + r.status));
    }
    return data;
  }

  async function loadAccounts() {
    const sel = $('catAccount');
    try {
      const d = await api('/api/catalog/accounts');
      sel.innerHTML = '';
      (d.accounts || []).forEach(a => {
        const o = document.createElement('option');
        o.value = a.name;
        o.textContent = a.name + (a.profile_name ? ' — ' + a.profile_name : '');
        sel.appendChild(o);
      });
      if (!d.accounts.length) {
        sel.innerHTML = '<option value="">нет аккаунтов с токеном</option>';
      }
    } catch (e) {
      sel.innerHTML = '<option value="">' + esc(e.message) + '</option>';
    }
  }

  function showErr(msg) {
    const el = $('catErr');
    el.innerHTML = esc(msg);
    el.classList.remove('hidden');
  }
  function clearErr() { $('catErr').classList.add('hidden'); }

  // --- выбор адреса через яндекс.Карты (как в сессиях Еды) ---
  let currentGeo = null;
  let asTimer = null;
  const addrInput = $('catAddr');
  const asBox = () => $('addrSuggest');
  addrInput.addEventListener('input', () => {
    currentGeo = null;
    clearTimeout(asTimer);
    const box = asBox();
    const q = addrInput.value.trim();
    if (!q || !window.ymaps) { box.classList.add('hidden'); return; }
    box.innerHTML = '<div class="mut" style="padding:10px">Поиск…</div>';
    box.classList.remove('hidden');
    asTimer = setTimeout(() => {
      ymaps.suggest(q, { results: 7 }).then(res => {
        if (!res || !res.length) {
          box.innerHTML = '<div class="mut" style="padding:10px">Ничего не найдено</div>';
          return;
        }
        box.innerHTML = '';
        res.forEach(s => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'as-box-suggest';
          const pos = s.items && s.items[0] && s.items[0].position;
          b.innerHTML = '<span class="pin">📍</span><span>' + esc(s.value) + '</span>';
          b.dataset.text = s.value;
          if (pos) b.dataset.pos = pos[0] + ',' + pos[1];
          b.addEventListener('click', async () => {
            box.classList.add('hidden');
            const text = b.dataset.text;
            addrInput.value = text;
            const p2 = (b.dataset.pos || '').split(',');
            let lat = p2[1] ? Number(p2[1]) : null;
            let lon = p2[0] ? Number(p2[0]) : null;
            if (lat == null && window.ymaps) {
              try {
                const r = await ymaps.geocode(text, { results: 1 });
                const o = r.geoObjects.get(0);
                const c = o ? o.geometry.getCoordinates() : null;
                if (c) { lat = c[0]; lon = c[1]; }
              } catch (e) { /* ignore */ }
            }
            if (lat != null) currentGeo = { lat, lon, label: text };
          });
          box.appendChild(b);
        });
      }).catch(() => box.classList.add('hidden'));
    }, 300);
  });
  document.addEventListener('click', (e) => {
    const box = asBox();
    if (!box.contains(e.target)) box.classList.add('hidden');
  });

  async function getCoords(addr) {
    // координаты через яндекс.Карты (надёжно для РФ); серверный геокодер — только fallback
    if (window.ymaps) {
      try {
        const r = await ymaps.geocode(addr, { results: 1 });
        const o = r.geoObjects.get(0);
        const c = o ? o.geometry.getCoordinates() : null;
        if (c) return { lat: c[0], lon: c[1], label: o.getAddressLine() || addr };
      } catch (e) { /* fallback ниже */ }
    }
    const g = await api('/api/catalog/geocode', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q: addr }),
    });
    return g;
  }

  $('catFind').addEventListener('click', async () => {
    const btn = $('catFind');
    clearErr();
    if (!$('catAccount').value) { showErr('Выберите аккаунт'); return; }
    const addr = addrInput.value.trim();
    if (!addr) { showErr('Введите адрес'); return; }
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>Поиск…';
    $('catShopsCard').classList.add('hidden');
    $('catResultsCard').classList.add('hidden');
    try {
      let g = currentGeo;
      $('catAddrLabel').textContent = g ? g.label : addr;
      if (!g) g = await getCoords(addr);
      $('catAddrLabel').textContent = g.label || addr;
      const s = await api('/api/catalog/shops', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: $('catAccount').value, lat: g.lat, lon: g.lon }),
      });
      renderShops(s.shops, g.lat, g.lon);
    } catch (e) {
      showErr(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Найти магазины';
    }
  });

  function renderShops(shops, lat, lon) {
    const box = $('catShops');
    box.innerHTML = '';
    if (!shops || !shops.length) {
      box.innerHTML = '<div class="empty">Магазины по этому адресу не найдены.<br>Проверьте адрес и выберите подсказку из списка.</div>';
      $('catShopsCard').classList.remove('hidden');
      return;
    }
    shops.forEach((s, i) => {
      const card = document.createElement('button');
      card.className = 'shop';
      card.style.animationDelay = Math.min(i * 40, 400) + 'ms';
      card.innerHTML =
        (s.logo ? '<img class="logo" src="' + esc(s.logo) + '" alt="">'
                : '<div class="noimg">🏬</div>') +
        '<span class="si"><b>' + esc(s.name) + '</b>' +
        '<span class="meta">' +
        (s.rating ? '<span class="star">★ ' + esc(fmt(s.rating)) + '</span>' : '') +
        '<span>' + esc(s.address) + '</span>' +
        '</span></span>';
      card.addEventListener('click', () => startParse(s, lat, lon));
      box.appendChild(card);
    });
    $('catShopsCard').classList.remove('hidden');
  }

  function startParse(s, lat, lon) {
    clearErr();
    if (!$('catAccount').value) { showErr('Выберите аккаунт'); return; }
    $('catShopsCard').classList.add('hidden');
    $('catResultsCard').classList.add('hidden');
    $('catProgressCard').classList.remove('hidden');
    $('catProgressTitle').textContent = 'Парсинг: ' + s.name;
    $('catProgressBar').style.width = '0%';
    $('catProgressMsg').textContent = 'запуск…';
    $('catProgressCard').dataset.slug = s.slug;
    $('catProgressCard').dataset.account = $('catAccount').value;
    $('catProgressCard').dataset.lat = lat;
    $('catProgressCard').dataset.lon = lon;
    api('/api/catalog/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account: $('catAccount').value, slug: s.slug, lat, lon }),
    }).then(d => pollParse(d.parse_id)).catch(e => {
      $('catProgressCard').classList.add('hidden');
      showErr(e.message);
    });
  }

  function pollParse(pid) {
    const stop = (msg) => {
      $('catProgressMsg').textContent = msg;
      setTimeout(() => $('catProgressCard').classList.add('hidden'), 600);
    };
    const t = setInterval(async () => {
      try {
        const d = await api('/api/catalog/parse/' + pid);
        $('catProgressBar').style.width = Math.round(d.frac * 100) + '%';
        $('catProgressMsg').textContent = (d.msg || '') + (d.total ? ' (' + d.done + '/' + d.total + ')' : '');
        if (d.state === 'done') {
          clearInterval(t);
          const sid = d.summary ? d.summary.store_id : null;
          stop('Готово ✓ ' + (d.summary ? d.summary.products + ' товаров, из них со скидкой: ' + d.summary.discounts : ''));
          loadStores(sid);
        } else if (d.state === 'error') {
          clearInterval(t);
          stop('');
          showErr(d.error || 'ошибка парсинга');
        }
      } catch (e) {
        clearInterval(t);
        $('catProgressCard').classList.add('hidden');
        showErr('Статус: ' + e.message);
      }
    }, 1500);
  }

  async function loadStores(storeId) {
    try {
      const d = await api('/api/catalog/stores');
      const wrap = $('catStores');
      wrap.innerHTML = '';
      const list = d.stores || [];
      if (storeId == null && list.length) storeId = list[0].id;
      wrap.dataset.activeId = '';
      (list).forEach(st => {
        const chip = document.createElement('button');
        chip.className = 'chip';
        chip.innerHTML = esc(st.name) + ' <span class="x">×</span> <span class="mut">' +
          esc(st.products_count) + '</span>';
        chip.title = 'Удалить каталог';
        chip.dataset.id = st.id;
        chip.addEventListener('click', async (e) => {
          if (e.target.classList.contains('x')) {
            if (!confirm('Удалить каталог ' + st.name + '?')) return;
            await api('/api/catalog/store/' + st.id, { method: 'DELETE' });
            loadStores();
            $('catView').innerHTML = '';
            return;
          }
          wrap.dataset.activeId = st.id;
          [...wrap.children].forEach(c => c.classList.toggle('active', c.dataset.id === st.id));
          openStore(st.id);
        });
        wrap.appendChild(chip);
      });
      if (storeId != null && list.some(st => String(st.id) === String(storeId))) {
        wrap.dataset.activeId = String(storeId);
        [...wrap.children].forEach(c => c.classList.toggle('active', c.dataset.id === String(storeId)));
        openStore(storeId);
      }
    } catch (e) { /* ignore */ }
  }

  function openStore(id) {
    storeIdActive = String(id);
    page = 1;
    locale();
  }

  async function locale() {
    if (!storeIdActive) { $('catView').innerHTML = ''; return; }
    const view = $('catView');
    view.innerHTML = '<div class="skgrid">' +
      Array(6).fill('<div class="sk"></div>').join('') + '</div>';
    $('catResultsCard').classList.remove('hidden');
    const offset = (page - 1) * PAGE;
    const qs = '/api/catalog/store/' + storeIdActive +
      '?discount=' + (state.discount ? 1 : 0) +
      (state.cat ? '&category=' + encodeURIComponent(state.cat) : '') +
      '&sort=' + encodeURIComponent(state.sort) +
      '&limit=' + PAGE + '&offset=' + offset;
    try {
      const d = await api(qs);
      total = d.total || 0;
      view.innerHTML = '';
      renderHeader(d.store, d.categories);
      const grid = document.createElement('div');
      grid.className = 'plist';
      grid.style.animation = 'fadeIn .22s ease both';
      renderRows(grid, d.products || []);
      view.appendChild(grid);
      renderPager();
    } catch (e) {
      view.innerHTML = '<div class="error">' + esc(e.message) + '</div>';
    }
  }

  function renderHeader(store, categories) {
    const view = $('catView');
    const control = document.createElement('div');
    control.className = 'controls';
    control.innerHTML =
      '<h3>' + esc(store.name) + '</h3>' +
      '<span class="mut">' + esc(store.address || '') + ' · ' +
      esc(store.products_count) + ' товаров' +
      (store.discounts_count > 0 ? ', со скидкой ' + esc(store.discounts_count) : '') +
      '</span>';
    const selWrap = document.createElement('div');
    selWrap.innerHTML = '<label>Категория</label><select id="catSel">' +
      '<option value="">Все категории</option>' +
      categories.map(c => '<option value="' + esc(c.uid) + '">' + esc(c.path || c.name) + '</option>').join('') +
      '</select>';
    const sortWrap = document.createElement('div');
    sortWrap.innerHTML = '<label>Сортировка</label><select id="sortSel2">' +
      '<option value="discount">По скидке</option>' +
      '<option value="default">По категориям</option>' +
      '<option value="price_asc">Цена ↑</option>' +
      '<option value="price_desc">Цена ↓</option>' +
      '</select>';
    const cbWrap = document.createElement('div');
    cbWrap.innerHTML = '<label><input type="checkbox" id="discFilter"> Только со скидкой</label>';
    control.appendChild(selWrap);
    control.appendChild(sortWrap);
    control.appendChild(cbWrap);
    view.appendChild(control);
    $('catSel').value = state.cat;
    $('sortSel2').value = state.sort;
    $('discFilter').checked = state.discount;
    $('catSel').addEventListener('change', () => { state.cat = $('catSel').value; page = 1; locale(); });
    $('sortSel2').addEventListener('change', () => { state.sort = $('sortSel2').value; page = 1; locale(); });
    $('discFilter').addEventListener('change', () => { state.discount = $('discFilter').checked; page = 1; locale(); });
  }

  function renderRows(grid, rows) {
    if (!rows.length) {
      grid.innerHTML = '<div class="empty">Товаров нет</div>';
      return;
    }
    rows.forEach((p, i) => {
      const row = document.createElement('div');
      row.className = 'prow';
      row.style.animationDelay = Math.min(i * 8, 120) + 'ms';
      const disc = p.is_discount && p.promo_price != null && p.discount_pct >= 1;
      const priceHtml = disc
        ? '<div class="pp"><span class="old">' + esc(fmt(p.price)) + ' ₽</span>' +
          '<span class="new">' + esc(fmt(p.promo_price)) + ' ₽</span></div>'
        : '<div class="pp"><span class="only">' + esc(fmt(p.price)) + ' ₽</span></div>';
      row.innerHTML =
        '<div class="pb' + (disc ? '' : ' off') + '">−' + esc(Math.round(p.discount_pct || 0)) + '%</div>' +
        '<div class="pi"><div class="pn">' + esc(p.name) + '</div>' +
        (p.weight ? '<div class="pw">' + esc(p.weight) + '</div>' : '') +
        '</div>' +
        priceHtml;
      grid.appendChild(row);
    });
  }

  function pageList(cur, max) {
    if (max <= 7) return Array.from({ length: max }, (_, i) => i + 1);
    const set = new Set([1, 2, cur - 1, cur, cur + 1, max - 1, max]);
    const arr = [...set].filter(p => p >= 1 && p <= max).sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    arr.forEach(p => {
      if (p - prev > 1) out.push('…');
      out.push(p);
      prev = p;
    });
    return out;
  }

  function renderPager() {
    const max = Math.max(1, Math.ceil(total / PAGE));
    const from = (page - 1) * PAGE + 1;
    const to = Math.min(total, page * PAGE);
    const div = document.createElement('div');
    div.className = 'pager';
    div.innerHTML = total ? '<div class="info">Товары ' + from + '–' + to + ' из ' + total + '</div>' : '';
    const nav = document.createElement('div');
    nav.className = 'pnav';
    const prev = document.createElement('button');
    prev.className = 'pbtn';
    prev.textContent = '‹ Назад';
    prev.disabled = page <= 1;
    prev.addEventListener('click', () => goPage(page - 1));
    nav.appendChild(prev);
    pageList(page, max).forEach(p => {
      if (p === '…') {
        const s = document.createElement('span');
        s.className = 'dots';
        s.textContent = '…';
        nav.appendChild(s);
        return;
      }
      const b = document.createElement('button');
      b.className = 'pbtn' + (p === page ? ' cur' : '');
      b.textContent = p;
      b.addEventListener('click', () => goPage(p));
      nav.appendChild(b);
    });
    const next = document.createElement('button');
    next.className = 'pbtn';
    next.textContent = 'Вперёд ›';
    next.disabled = page >= max;
    next.addEventListener('click', () => goPage(page + 1));
    nav.appendChild(next);
    div.appendChild(nav);
    $('catView').appendChild(div);
  }

  function goPage(p) {
    const max = Math.max(1, Math.ceil(total / PAGE));
    if (p < 1 || p > max || p === page) return;
    page = p;
    const card = $('catResultsCard');
    const top = card.getBoundingClientRect().top + window.scrollY - 70;
    locale();
    window.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' });
  }

  loadAccounts();
  loadStores();
})();