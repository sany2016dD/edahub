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
    shops.forEach(s => {
      const card = document.createElement('button');
      card.className = 'shop';
      card.innerHTML =
        (s.logo ? '<img src="' + esc(s.logo) + '" alt="">'
                : '<div class="noimg">🏬</div>') +
        '<b>' + esc(s.name) + '</b>' +
        '<div class="meta">' +
        (s.rating ? '<span class="star">★ ' + esc(fmt(s.rating)) + '</span>' : '') +
        '<span>' + esc(s.address) + '</span>' +
        '</div>';
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
          esc(st.products_count) + ' тов.</span>';
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
          locale(st.id);
        });
        wrap.appendChild(chip);
      });
      if (storeId != null && list.some(st => String(st.id) === String(storeId))) {
        wrap.dataset.activeId = String(storeId);
        [...wrap.children].forEach(c => c.classList.toggle('active', c.dataset.id === String(storeId)));
        locale(storeId);
      }
    } catch (e) { /* ignore */ }
  }

  async function locale(storeId) {
    if (!storeId) { $('catView').innerHTML = ''; return; }
    const view = $('catView');
    const discount = $('discFilter') ? $('discFilter').checked : false;
    const cat = $('catSel') ? $('catSel').value : '';
    const sort = $('sortSel2') ? $('sortSel2').value : 'default';
    const qs = '/api/catalog/store/' + storeId +
      '?discount=' + (discount ? 1 : 0) +
      (cat ? '&category=' + encodeURIComponent(cat) : '') +
      '&sort=' + encodeURIComponent(sort);
    try {
      const d = await api(qs);
      renderView(d.store, d.categories, d.products);
    } catch (e) {
      view.innerHTML = '<div class="error">' + esc(e.message) + '</div>';
    }
  }

  function renderView(store, categories, products) {
    const view = $('catView');
    view.innerHTML = '';
    const control = document.createElement('div');
    control.className = 'controls';
    control.innerHTML =
      '<h3 style="margin-right:8px">' + esc(store.name) + '</h3>' +
      '<span class="mut">' + esc(store.address || '') + '</span>' +
      '<span class="mut"> · ' + esc(store.products_count) + ' товаров</span>';
    if (store.discounts_count > 0) {
      control.innerHTML += '<span class="mut"> · со скидкой: <b style="color:var(--green)">' +
        esc(store.discounts_count) + '</b></span>';
    }
    const selWrap = document.createElement('div');
    selWrap.innerHTML = '<label>Категория</label><select id="catSel">' +
      '<option value="">Все категории</option>' +
      categories.map(c => '<option value="' + esc(c.uid) + '">' + esc(c.path || c.name) + '</option>').join('') +
      '</select>';
    const sortWrap = document.createElement('div');
    sortWrap.innerHTML = '<label>Сортировка</label><select id="sortSel2">' +
      '<option value="discount">По скидке ▾</option>' +
      '<option value="default">По категориям</option>' +
      '<option value="price_asc">Цена по возрастанию</option>' +
      '<option value="price_desc">Цена по убыванию</option>' +
      '</select>';
    const cbWrap = document.createElement('div');
    cbWrap.innerHTML = '<label><input type="checkbox" id="discFilter"> Только со скидкой</label>';
    control.appendChild(selWrap);
    control.appendChild(sortWrap);
    control.appendChild(cbWrap);
    view.appendChild(control);
    $('catSel').addEventListener('change', () => locale($('catStores').dataset.activeId));
    $('sortSel2').addEventListener('change', () => locale($('catStores').dataset.activeId));
    $('discFilter').addEventListener('change', () => locale($('catStores').dataset.activeId));

    const grid = document.createElement('div');
    grid.className = 'pgrid';
    if (!products.length) grid.innerHTML = '<div class="empty">Товаров нет</div>';
    (products || []).forEach(p => {
      const card = document.createElement('div');
      card.className = 'pcard';
      const img = p.picture ? '<img loading="lazy" src="' + esc(p.picture) + '" alt="">'
                            : '<div class="noimg">🛒</div>';
      const priceHtml = (p.promo_price != null && p.is_discount)
        ? '<div class="pp"><span class="old">' + esc(fmt(p.price)) + ' ₽</span>' +
          '<span class="new">' + esc(fmt(p.promo_price)) + ' ₽</span></div>'
        : '<div class="pp"><span class="no-disc">' + esc(fmt(p.price)) + ' ₽</span></div>';
      card.innerHTML =
        '<div class="ph">' + img +
        (p.is_discount ? '<div class="badge">−' + esc(Math.round(p.discount_pct || 0)) + '%</div>' : '') +
        '</div>' +
        '<div class="pcat">' + esc(p.category_uid || '') + '</div>' +
        '<div class="pn">' + esc(p.name) + '</div>' +
        (p.weight ? '<div class="pw">' + esc(p.weight) + '</div>' : '') +
        priceHtml;
      grid.appendChild(card);
    });
    view.appendChild(grid);
    $('catResultsCard').classList.remove('hidden');
  }

  loadAccounts();
  loadStores();
})();