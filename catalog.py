# catalog.py — парсер магазинов Яндекса Еды:
# адрес → координаты → магазины → категории и товары (цена, скидка) → база.
# Отдельная страница /catalog показывает результат как список со скидками.
import sys, os, json, time, re, uuid, threading
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import eda

MAX_ITEMS_PER_CAT = 200
PARSE_WORKERS = 4
_EXT = threading.Lock()


def _num(v):
    """float из числа или строки вида '230.00'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _abs_img(url, size='300x300'):
    if not url:
        return ''
    u = str(url).replace('{w}x{h}', size)
    if u.startswith('/'):
        u = 'https://eda.yandex.ru' + u
    return u


def _img_uri(obj):
    """Извлечь uri картинки из dict {uri|url} или списка таких dict / строки."""
    if not obj:
        return ''
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        for x in obj:
            u = _img_uri(x)
            if u:
                return u
        return ''
    if isinstance(obj, dict):
        return str(obj.get('uri') or obj.get('url') or '')
    return ''


# ---------- таблицы ----------

def init_db():
    conn = core._db()
    try:
        if core.USE_PG:
            core._ex(conn, '''
                CREATE TABLE IF NOT EXISTS cat_stores (
                    id SERIAL PRIMARY KEY,
                    slug TEXT NOT NULL, name TEXT, logo TEXT,
                    lat REAL, lon REAL, address TEXT, region TEXT,
                    account TEXT, status TEXT DEFAULT 'parsing',
                    error TEXT, products_count INTEGER DEFAULT 0,
                    discounts_count INTEGER DEFAULT 0,
                    categories_count INTEGER DEFAULT 0, created_at TEXT
                )
            ''')
        else:
            core._ex(conn, '''
                CREATE TABLE IF NOT EXISTS cat_stores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL, name TEXT, logo TEXT,
                    lat REAL, lon REAL, address TEXT, region TEXT,
                    account TEXT, status TEXT DEFAULT 'parsing',
                    error TEXT, products_count INTEGER DEFAULT 0,
                    discounts_count INTEGER DEFAULT 0,
                    categories_count INTEGER DEFAULT 0, created_at TEXT
                )
            ''')
        if core.USE_PG:
            core._ex(conn, '''
                CREATE TABLE IF NOT EXISTS cat_categories (
                    id SERIAL PRIMARY KEY,
                    store_id INTEGER, uid TEXT, name TEXT,
                    parent_uid TEXT, path TEXT, sort INTEGER
                )
            ''')
        else:
            core._ex(conn, '''
                CREATE TABLE IF NOT EXISTS cat_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER, uid TEXT, name TEXT,
                    parent_uid TEXT, path TEXT, sort INTEGER
                )
            ''')
        if core.USE_PG:
            core._ex(conn, '''
                CREATE TABLE IF NOT EXISTS cat_products (
                    id SERIAL PRIMARY KEY,
                    store_id INTEGER, category_uid TEXT, item_uid TEXT,
                    name TEXT, price REAL, promo_price REAL,
                    discount_pct REAL, discount_label TEXT,
                    weight TEXT, picture TEXT, qty REAL, unit TEXT,
                    is_discount INTEGER DEFAULT 0, sort INTEGER
                )
            ''')
        else:
            core._ex(conn, '''
                CREATE TABLE IF NOT EXISTS cat_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER, category_uid TEXT, item_uid TEXT,
                    name TEXT, price REAL, promo_price REAL,
                    discount_pct REAL, discount_label TEXT,
                    weight TEXT, picture TEXT, qty REAL, unit TEXT,
                    is_discount INTEGER DEFAULT 0, sort INTEGER
                )
            ''')
        conn.commit()
    finally:
        conn.close()


def _insert(conn, table, fields, values):
    cols = ','.join(fields)
    ph = ','.join(['%s'] * len(fields))
    sql = f'INSERT INTO {table} ({cols}) VALUES ({ph})'
    if core.USE_PG:
        cur = core._ex(conn, sql + ' RETURNING id', values)
        conn.commit()
        row = cur.fetchone()
        return row['id'] if row else None
    cur = core._ex(conn, sql, values)
    conn.commit()
    return cur.lastrowid


def _insert_many(conn, table, fields, rows, chunk=200):
    if not rows:
        return
    cols = ','.join(fields)
    ph = ','.join(['%s'] * len(fields))
    sql = f'INSERT INTO {table} ({cols}) VALUES ({ph})'
    if core.USE_PG:
        for i in range(0, len(rows), chunk):
            with conn.cursor() as cur:
                cur.executemany(sql, rows[i:i + chunk])
    else:
        qsql = sql.replace('%s', '?')
        for i in range(0, len(rows), chunk):
            conn.executemany(qsql, rows[i:i + chunk])
    conn.commit()


# ---------- геокодер ----------

_GEO_CACHE = {}


def geocode(q):
    """Адрес → координаты (Nominatim/OSM, без ключа). Кэш в памяти."""
    q = (q or '').strip()
    if not q:
        raise RuntimeError('адрес не указан')
    with _EXT:
        if q in _GEO_CACHE:
            return dict(_GEO_CACHE[q])
    try:
        r = core.s.get('https://nominatim.openstreetmap.org/search',
                       params={'q': q, 'format': 'json', 'limit': 1,
                               'accept-language': 'ru'},
                       headers={'User-Agent': 'magnit-catalog/1.0'}, timeout=20)
        d = r.json()
    except Exception as e:
        raise RuntimeError(f'геокодер: {e}')
    if not isinstance(d, list) or not d:
        raise RuntimeError(f'адрес не найден: {q}')
    out = {'lat': float(d[0]['lat']), 'lon': float(d[0]['lon']),
           'label': d[0].get('display_name', q)}
    with _EXT:
        _GEO_CACHE[q] = dict(out)
    return out


# ---------- список магазинов по адресу ----------

def _nearby_slugs(acc, lat, lon):
    """Ближайшие магазины (retail) через layout-constructor с заданными координатами."""
    try:
        if eda._use_web(acc):
            d = eda._web_call(acc, 'POST', '/eats/v1/layout-constructor/v1/layout',
                              json_body={'location': {'latitude': lat, 'longitude': lon}})
        else:
            d = eda._eda_call(acc, 'POST', '/eats/v1/layout-constructor/v1/layout', lat, lon,
                              json_body={'location': {'latitude': lat, 'longitude': lon}})
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
    slugs = []
    try:
        carousels = (((d.get('data') or {}).get('mini_places_carousels')) or [])
        for c in carousels:
            for p in ((c.get('payload') or {}).get('places') or []):
                sl = p.get('slug') if isinstance(p, dict) else None
                if sl and sl not in slugs:
                    slugs.append(sl)
    except Exception:
        pass
    if not slugs:
        txt = json.dumps(d, ensure_ascii=False)
        for m in re.finditer(r'"slug"\s*:\s*"([a-z0-9_]+)"', txt):
            sl = m.group(1)
            if any(k in sl for k in ('magnit', 'pater', 'pyater', 'perek',
                                     'fix', 'retail', 'shop')) and sl not in slugs:
                slugs.append(sl)
    return slugs


def list_shops(account, lat, lon, limit=30):
    """Магазины, доступные по адресу (layout + поиск, business='shop')."""
    acc = eda.get_eda_account(account) if isinstance(account, str) else account
    if not acc:
        raise RuntimeError(f'аккаунт "{account}" не найден')
    slugs = []
    for sl in _nearby_slugs(acc, lat, lon):
        if sl not in slugs:
            slugs.append(sl)
    try:
        r = eda.search_restaurants(acc, lat=lat, lon=lon)
        for b in (r.get('blocks') or []):
            if b.get('type') != 'places':
                continue
            for pl in (b.get('payload') or []):
                if isinstance(pl, dict) and pl.get('business') in ('shop', 'retail'):
                    sl = pl.get('slug')
                    if sl and sl not in slugs:
                        slugs.append(sl)
    except Exception:
        pass
    out = []
    for sl in slugs[:limit]:
        try:
            info = eda.shop_info(acc, sl, lat=lat, lon=lon)
            place = ((((info or {}).get('payload') or {}).get('foundPlace') or {}).get('place') or {})
            addr = place.get('address') or {}
            loc = addr.get('location') or {}
            out.append({
                'slug': sl,
                'name': place.get('name') or sl,
                'logo': _abs_img(_img_uri(place.get('logo')) or _img_uri(place.get('picture'))),
                'rating': place.get('rating'),
                'rating_count': place.get('ratingCount'),
                'address': addr.get('short') or '',
                'business': place.get('business') or 'shop',
                'latitude': loc.get('latitude'),
                'longitude': loc.get('longitude'),
            })
        except Exception:
            out.append({'slug': sl, 'name': sl, 'logo': '', 'rating': None,
                        'rating_count': None, 'address': '',
                        'business': 'shop', 'latitude': None, 'longitude': None})
    return out


# ---------- категории ----------

def _flatten_cats(cat_list, parent_uid=None, path='', acc_path=''):
    """Разложить дерево категорий в плоский список (uid, name, parent, path)."""
    out = []
    for i, c in enumerate(cat_list):
        uid = str(c.get('uid') or c.get('id') or '')
        name = c.get('name') or uid
        p = acc_path + (name + ' › ' if acc_path else name)
        out.append({'uid': uid, 'name': name,
                    'parent_uid': str(c.get('parentId')) if c.get('parentId') is not None else ('' if not parent_uid else parent_uid),
                    'path': p})
        for ch in (c.get('children') or []):
            out.extend(_flatten_cats([ch], parent_uid=uid, path=path, acc_path=(acc_path + name + ' / ' if acc_path else name + ' / ')))
    return out


# ---------- товары ----------

def _goods_cat(acc, slug, uid, lat, lon):
    """Товары одной категории (get-categories, до MAX_ITEMS_PER_CAT)."""
    cats = [{'uid': str(uid), 'min_items_count': 1, 'max_items_count': MAX_ITEMS_PER_CAT}]
    g = eda._eda_call(acc, 'POST', '/api/v2/menu/goods/get-categories', lat, lon,
                      json_body={'slug': slug, 'categories': cats})
    items = []
    for cg in (g.get('categories') or []):
        items.extend(cg.get('items') or [])
    return items


def _product_row(cat_uid, it, sort):
    name = it.get('name') or ''
    price = _num(it.get('price', it.get('decimalPrice')))
    promo = _num(it.get('promoPrice', it.get('decimalPromoPrice')))
    if promo is not None and price and promo >= price:
        promo = None
    discount_pct = None
    label = ''
    for pt in (it.get('promoTypes') or []):
        if pt.get('type') == 'price_discount':
            label = (pt.get('text') or '').strip()
            m = re.search(r'(-?\d+)', label)
            if m:
                discount_pct = float(m.group(1).lstrip('-'))
            break
    if promo is not None and price and discount_pct is None:
        discount_pct = round((1 - promo / price) * 100, 1)
    pic = _img_uri(it.get('picture'))
    item_uid = it.get('uid') or it.get('public_id') or it.get('id') or it.get('slug') or ''
    qty = _num(it.get('qty'))
    unit = it.get('unit') or ''
    return {
        'category_uid': cat_uid, 'item_uid': str(item_uid),
        'name': name, 'price': price, 'promo_price': promo,
        'discount_pct': discount_pct, 'discount_label': label,
        'weight': it.get('weight') or '',
        'picture': _abs_img(pic), 'qty': qty, 'unit': unit,
        'is_discount': 1 if (promo is not None and discount_pct and discount_pct >= 1) else 0,
        'sort': sort,
    }


# ---------- полный парсер магазина ----------

def parse_store(account, slug, lat, lon, progress=None):
    """Парсер магазина: инфо → все категории → товары всех категорий → база.

    progress(msg, frac, done, total).
    Возвращает {'store_id': int, 'summary': {...}}.
    """
    acc = eda.get_eda_account(account) if isinstance(account, str) else account
    if not acc:
        raise RuntimeError(f'аккаунт "{account}" не найден')
    info = eda.shop_info(acc, slug, lat, lon)
    place = ((((info or {}).get('payload') or {}).get('foundPlace') or {}).get('place') or {})
    addr = place.get('address') or {}
    loc = addr.get('location') or {}
    name = place.get('name') or slug
    lat2 = loc.get('latitude') if loc.get('latitude') is not None else lat
    lon2 = loc.get('longitude') if loc.get('longitude') is not None else lon

    conn = core._db()
    try:
        store_id = _insert(conn, 'cat_stores',
                           ['slug', 'name', 'logo', 'lat', 'lon', 'address',
                            'region', 'account', 'status', 'created_at'],
                           [slug, name, _abs_img(_img_uri(place.get('logo')) or _img_uri(place.get('picture'))),
                            lat2, lon2, addr.get('short') or '', addr.get('city') or '',
                            account, 'parsing', time.strftime('%Y-%m-%d %H:%M:%S')])
    finally:
        conn.close()

    # категории (flat-список дерева)
    if progress:
        progress('загружаем категории…', 0.03, 0, 0)
    cats = eda.shop_categories(acc, slug, lat, lon)
    cat_list = ((cats.get('payload') or {}).get('categories') or [])
    flat = _flatten_cats(cat_list)
    cat_map = {c['uid']: c for c in flat}
    uids = list(cat_map.keys())

    cat_rows = [(store_id, c['uid'], c['name'], c['parent_uid'],
                 c['path'][:300], i) for i, c in enumerate(flat)]
    conn = core._db()
    try:
        _insert_many(conn, 'cat_categories',
                     ['store_id', 'uid', 'name', 'parent_uid', 'path', 'sort'],
                     cat_rows)
    finally:
        conn.close()

    # товары всех категорий (параллельно)
    total = len(uids) or 1
    done = 0
    product_rows = []

    rows_to_save = []

    def work(uid):
        try:
            return uid, _goods_cat(acc, slug, uid, lat, lon)
        except Exception:
            return uid, []

    with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as ex:
        futs = [ex.submit(work, u) for u in uids]
        for f in futs:
            uid, items = f.result()
            for i, it in enumerate(items):
                rows_to_save.append(_product_row(uid, it, i))
            done += 1
            if progress:
                progress(f'товары {done}/{total}', 0.03 + 0.9 * done / total,
                         done, total)
    product_rows = rows_to_save

    discounts = sum(1 for r in product_rows if r['is_discount'])
    conn = core._db()
    try:
        _insert_many(conn, 'cat_products',
                     ['store_id', 'category_uid', 'item_uid', 'name', 'price',
                      'promo_price', 'discount_pct', 'discount_label',
                      'weight', 'picture', 'qty', 'unit', 'is_discount', 'sort'],
                     [[store_id, r['category_uid'], r['item_uid'], r['name'],
                       r['price'], r['promo_price'], r['discount_pct'],
                       r['discount_label'], r['weight'], r['picture'],
                       r['qty'], r['unit'], r['is_discount'], r['sort']]
                      for r in product_rows])
        core._ex(conn, '''
            UPDATE cat_stores SET status=%s, products_count=%s,
                   discounts_count=%s, categories_count=%s
            WHERE id=%s
        ''', ('done', len(product_rows), discounts, len(uids), store_id))
        conn.commit()
    finally:
        conn.close()
    if progress:
        progress('готово ✓', 1.0, total, total)
    return {'store_id': store_id, 'summary': {
        'name': name, 'products': len(product_rows), 'discounts': discounts,
        'categories': len(uids),
    }}


# ---------- чтение ----------

def list_stores(limit=50):
    conn = core._db()
    try:
        cur = core._ex(conn, '''
            SELECT id, slug, name, logo, lat, lon, address, account,
                   status, error, products_count, discounts_count,
                   categories_count, created_at
            FROM cat_stores ORDER BY id DESC LIMIT %s
        ''', (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def store_data(store_id):
    conn = core._db()
    try:
        cur = core._ex(conn, '''
            SELECT id, slug, name, logo, lat, lon, address, account,
                   status, error, products_count, discounts_count,
                   categories_count, created_at
            FROM cat_stores WHERE id=%s
        ''', (store_id,))
        store = [dict(r) for r in cur.fetchall()]
        if not store:
            return None
        store = store[0]
        cur = core._ex(conn, '''
            SELECT uid, name, parent_uid, path, sort FROM cat_categories
            WHERE store_id=%s ORDER BY sort
        ''', (store_id,))
        cats = [dict(r) for r in cur.fetchall()]
        return {'store': store, 'categories': cats}
    finally:
        conn.close()


def store_products(store_id, discount_only=False, category=None, sort='default', limit=60, offset=0):
    conn = core._db()
    try:
        sql = ('SELECT id, category_uid, item_uid, name, price, promo_price,'
               ' discount_pct, discount_label, weight, picture, qty, unit,'
               ' is_discount FROM cat_products WHERE store_id=%s')
        params = [store_id]
        if discount_only:
            sql += ' AND is_discount=1'
        if category:
            sql += ' AND category_uid=%s'
            params.append(str(category))
        order = {
            'default': 'sort',
            'discount': 'CASE WHEN discount_pct IS NULL THEN 0 ELSE discount_pct END DESC, sort',
            'price_asc': 'COALESCE(promo_price, price) ASC NULLS LAST, sort',
            'price_desc': 'COALESCE(promo_price, price) DESC NULLS LAST, sort',
        }.get(sort, 'sort')
        if core.USE_PG:
            sql += f' ORDER BY {order} LIMIT %s OFFSET %s'
        else:
            sql += f' ORDER BY {order.replace("NULLS LAST", "")} LIMIT %s OFFSET %s'
        params.extend([limit, offset])
        cur = core._ex(conn, sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def store_products_total(store_id, discount_only=False, category=None):
    conn = core._db()
    try:
        sql = 'SELECT COUNT(*) AS n FROM cat_products WHERE store_id=%s'
        params = [store_id]
        if discount_only:
            sql += ' AND is_discount=1'
        if category:
            sql += ' AND category_uid=%s'
            params.append(str(category))
        cur = core._ex(conn, sql, params)
        row = cur.fetchone()
        return row['n'] if row else 0
    finally:
        conn.close()


def delete_store(store_id):
    conn = core._db()
    try:
        core._ex(conn, 'DELETE FROM cat_products WHERE store_id=%s', (store_id,))
        core._ex(conn, 'DELETE FROM cat_categories WHERE store_id=%s', (store_id,))
        core._ex(conn, 'DELETE FROM cat_stores WHERE id=%s', (store_id,))
        conn.commit()
    finally:
        conn.close()


# ---------- фоновый парсер (для веб-ui) ----------

PARSES = {}
_LOCK = threading.Lock()


def run_parse_async(account, slug, lat, lon):
    pid = uuid.uuid4().hex[:12]
    PARSES[pid] = {'state': 'running', 'msg': 'старт…', 'frac': 0.0,
                   'done': 0, 'total': 0, 'error': None, 'store_id': None,
                   'summary': None}

    def progress(msg, frac, done, total):
        st = PARSES.get(pid)
        if st:
            st['msg'] = msg
            st['frac'] = frac
            st['done'] = done
            st['total'] = total

    def work():
        try:
            res = parse_store(account, slug, lat, lon, progress=progress)
            st = PARSES.get(pid)
            if st:
                st['store_id'] = res['store_id']
                st['summary'] = res['summary']
                st['state'] = 'done'
        except Exception as e:
            st = PARSES.get(pid)
            if st:
                st['state'] = 'error'
                st['error'] = str(e)

    threading.Thread(target=work, daemon=True).start()
    return pid


def parse_status(pid):
    st = PARSES.get(pid)
    if not st:
        return {'state': 'unknown'}
    return dict(st)


init_db()