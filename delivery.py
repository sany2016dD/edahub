import sys, os, json, uuid, time, re, random, contextlib, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import requests

# ============================================================
#  Яндекс.Делливери (Delivery): доставка.
#
#  Реальные эндпоинты и живые учётные данные получены из mitm-перехвата
#  флоу в мобильном приложении com.deliveryclub 26.34.0 (Android 12).
#
#  Авторизация: OAuth Bearer-токен + cookie Eats-Session (живая сессия)
#  + набор app/device заголовков (x-platform=dc_app_android и пр.).
#  Хост API: dc.eda.yandex.net.
# ============================================================

DELIVERY_ACCOUNTS_FILE = os.path.join(core.DATA_DIR, 'delivery_accounts.json')
DELIVERY_SESSIONS_FILE = os.path.join(core.DATA_DIR, 'delivery_sessions.json')

DC_HOST = 'https://dc.eda.yandex.net'

# Обязательные query-параметры всех запросов приложения (моб-конфиг).
DC_MOBCF = 'russia%25delivery_default_3%25default'
DC_MOBPR = 'delivery_default_3_EATS_BASE_0'
DC_Q = f'mobcf={DC_MOBCF}&mobpr={DC_MOBPR}'

# Дефолтная точка: Омск, проспект Мира, 33 (из захвата).
DEFAULT_LAT = 55.028785
DEFAULT_LON = 73.275838

# Валидный address.uri для дефолтной точки (из захвата go-checkout).
# Без uri go-checkout не может разобрать адрес ("cannot be parsed as a variant").
DEFAULT_URI = ('ymapsbm1://geo?data='
               'Cgg1NzE1ODgyMxI10KDQvtGB0YHQuNGPLCDQntC80YHQuiwg0L_RgNC-0YHQv9C10LrRgiDQnNC40YDQsCwgMzMiCg06jZJCFXkdXEI,')


# ---------- хранилище аккаунтов (живая dc-сессия) ----------

def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _accs_lock():
    return contextlib.nullcontext()


def load_delivery_accounts():
    return _read_json(DELIVERY_ACCOUNTS_FILE).get('accounts', [])


def save_delivery_accounts(accs):
    _write_json_atomic(DELIVERY_ACCOUNTS_FILE, {'accounts': accs})


def get_delivery_account(name):
    return next((a for a in load_delivery_accounts() if a.get('name') == name), None)


def create_delivery_account(name, creds, lat=None, lon=None):
    name = (name or '').strip()
    if not name:
        raise RuntimeError('name required')
    if get_delivery_account(name):
        raise RuntimeError(f'аккаунт "{name}" уже существует')
    accs = load_delivery_accounts()
    acc = {
        'name': name,
        'lat': float(lat) if lat is not None else DEFAULT_LAT,
        'lon': float(lon) if lon is not None else DEFAULT_LON,
        'creds': creds or {},
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    accs.append(acc)
    save_delivery_accounts(accs)
    return acc


def update_delivery_account(name, creds=None, lat=None, lon=None):
    accs = load_delivery_accounts()
    for a in accs:
        if a.get('name') == name:
            if creds is not None:
                a['creds'] = creds
            if lat is not None:
                a['lat'] = float(lat)
            if lon is not None:
                a['lon'] = float(lon)
            save_delivery_accounts(accs)
            return a
    raise RuntimeError(f'аккаунт "{name}" не найден')


def delete_delivery_account(name):
    accs = load_delivery_accounts()
    out = [a for a in accs if a.get('name') != name]
    if len(out) == len(accs):
        raise RuntimeError(f'аккаунт "{name}" не найден')
    save_delivery_accounts(out)
    return True


# ---------- хранилище сессий доступа ----------

def load_delivery_sessions():
    return _read_json(DELIVERY_SESSIONS_FILE).get('sessions', {})


def save_delivery_sessions(sess):
    _write_json_atomic(DELIVERY_SESSIONS_FILE, {'sessions': sess})


def get_delivery_session(token):
    if not token:
        return None
    s = load_delivery_sessions().get(token)
    if not s or not s.get('active'):
        return None
    if s.get('expires_at') and s['expires_at'] < time.strftime('%Y-%m-%d %H:%M:%S'):
        return None
    return s


def get_delivery_session_account(token):
    s = get_delivery_session(token)
    if not s:
        return None, None
    acc = get_delivery_account(s.get('account', ''))
    return s, acc


def touch_delivery_session(token):
    sess = load_delivery_sessions()
    if token in sess:
        sess[token]['last_seen'] = time.strftime('%Y-%m-%d %H:%M:%S')
        save_delivery_sessions(sess)


def create_delivery_session(name, account, hours=24):
    name = (name or '').strip()
    account = (account or '').strip()
    if not name or not account:
        raise RuntimeError('name and account required')
    if not get_delivery_account(account):
        raise RuntimeError(f'аккаунт "{account}" не найден')
    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    now = time.time()
    sess = load_delivery_sessions()
    sess[token] = {
        'name': name,
        'account': account,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
        'expires_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now + hours * 3600)),
        'last_seen': None,
        'active': True,
        'address': None,
        'cart': None,
        'order': None,
    }
    save_delivery_sessions(sess)
    return token


def delete_delivery_session(token):
    sess = load_delivery_sessions()
    if token in sess:
        sess[token]['active'] = False
        save_delivery_sessions(sess)
        return True
    return False


def set_delivery_session_address(token, address):
    sess = load_delivery_sessions()
    if token not in sess:
        raise RuntimeError('сессия не найдена')
    sess[token]['address'] = address or None
    save_delivery_sessions(sess)
    return sess[token]['address']


def get_delivery_address(token):
    s = get_delivery_session(token)
    return (s or {}).get('address')


# ---------- HTTP-клиент dc.eda.yandex.net ----------

def _creds(acc):
    c = acc.get('creds') or {}
    return c


def _headers(acc, lat=None, lon=None, extra=None):
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    c = _creds(acc)
    h = {
        'accept-encoding': 'gzip',
        'accept-language': c.get('accept_language', 'ru'),
        'content-type': 'application/json; charset=utf-8',
        'md-native': 'md-native',
        'user-agent': c.get('user_agent', 'DeliveryClubApp/26.34.0.5000259 (DT/Mobile;PN/Android;PV/12;DI/Samsung a53x SM-A536E;SC/900x1600@1)'),
        'x-android-platform-services-type': c.get('x_android_platform_services_type', 'google'),
        'x-app-version': c.get('x_app_version', '26.34.0'),
        'x-appmetrica-deviceid': c.get('x_appmetrica_deviceid', ''),
        'x-appmetrica-uuid': c.get('x_appmetrica_uuid', ''),
        'x-app-theme': 'light',
        'x-client-session': c.get('x_client_session', ''),
        'x-code-version': c.get('x_code_version', '5000259'),
        'x-device-brand': c.get('x_device_brand', 'Samsung'),
        'x-device-id': c.get('x_device_id', ''),
        'x-device-manufacturer': c.get('x_device_manufacturer', 'Samsung'),
        'x-device-model': c.get('x_device_model', 'SM-A536E'),
        'x-mob-id': c.get('x_mob_id', ''),
        'x-os-version': c.get('x_os_version', '12'),
        'x-platform': 'dc_app_android',
        'x-tracker-id': c.get('x_tracker_id', ''),
        'x-ya-client-time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'x-ya-coordinates': f'latitude={lat},longitude={lon}',
        'x-yandex-uid': str(c.get('x_yandex_uid', '')),
    }
    if c.get('authorization'):
        h['authorization'] = c['authorization']
    if c.get('cookie'):
        h['cookie'] = c['cookie']
    if extra:
        h.update(extra)
    return h


def _dc_call(account, method, path, json_body=None, params=None, lat=None, lon=None, timeout=30):
    acc = get_delivery_account(account) if isinstance(account, str) else account
    if not acc:
        raise RuntimeError(f'аккаунт "{account}" не найден')
    hdrs = _headers(acc, lat, lon)
    url = DC_HOST + path
    # соединяем фикс-query (mobcf/mobpr) с дополнительными params
    qparams = dict(DC_Q.split('&') for _ in [])  # noqa
    qp = {}
    for kv in DC_Q.split('&'):
        k, _, v = kv.partition('=')
        qp[k] = v
    if params:
        qp.update(params)
    try:
        r = requests.request(method, url, headers=hdrs, json=json_body,
                             params=qp, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f'Делливери: сеть ({method} {path}): {e}')
    if r.status_code == 204:
        return {'ok': True}
    if r.status_code >= 400:
        raise RuntimeError(f'Делливери: HTTP {r.status_code} на {method} {path}: {r.text[:300]}')
    try:
        return r.json()
    except Exception:
        return {'_status': r.status_code, '_text': r.text[:1000]}


# ---------- API-функции (живые) ----------

def layout(account, lat=None, lon=None):
    """Главная лента (каталог): блоки с местами/баннерами."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    body = {"location": {"latitude": lat, "longitude": lon}, "filters_v2": {"filters": []}}
    return _dc_call(acc, 'POST', '/eats/v1/layout-constructor/v1/layout', json_body=body, lat=lat, lon=lon)


def _str_val(v):
    """Извлечь строку из поля вида {'value': ...} либо строки."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if 'value' in v:
            return _str_val(v['value'])
        if 'text' in v:
            return _str_val(v['text'])
    return ''


def _img_url(u):
    """Полный URL картинки из поля picture ({image: '/images/...'} или {url: 'https...'})."""
    if not u:
        return ''
    if isinstance(u, dict):
        if u.get('url'):
            return u['url']
        if u.get('image'):
            return _img_url(u['image'])
        for k in ('light', 'dark'):
            if u.get(k):
                return _img_url(u[k])
        return ''
    s = str(u)
    if s.startswith('/images/'):
        return 'https://eda.yandex' + s
    return s


def _place_eta(p):
    lm = p.get('left_meta')
    if not lm:
        return ''
    if isinstance(lm, dict):
        lm = [lm]
    for it in lm:
        pl = it.get('payload') or it
        t = _str_val(pl.get('text'))
        if t:
            return t
    return ''


def parse_layout_places(data):
    """Собрать список мест (рестораны/магазины) из layout-ответа для UI."""
    places = []
    if not isinstance(data, dict):
        return places
    data = data.get('data', data)
    carousels = data.get('places_v2_medium_carousels') or []
    lists = data.get('places_v2_lists') or []
    mini = data.get('mini_places_carousels') or []
    for grp in list(carousels) + list(lists):
        payload = grp.get('payload') or {}
        for p in payload.get('places') or []:
            slug = p.get('slug') or p.get('place_slug')
            brand = p.get('brand') or {}
            name = _str_val(p.get('name')) or brand.get('name') or ''
            places.append({
                'slug': slug,
                'name': name,
                'logo': _img_url(p.get('picture') or p.get('logo')),
                'eta': _place_eta(p),
                'business': p.get('business') or brand.get('business'),
            })
    # mini_places_carousels: другая структура (name — строка, картинки в media)
    for grp in mini:
        payload = grp.get('payload') or {}
        for p in payload.get('places') or []:
            slug = p.get('slug') or p.get('place_slug')
            brand = p.get('brand') or {}
            name = p.get('name') if isinstance(p.get('name'), str) else _str_val(p.get('name'))
            name = name or brand.get('name') or ''
            # картинка: media.photos[0].uri
            logo = ''
            media = p.get('media') or {}
            photos = media.get('photos') or []
            if photos:
                logo = photos[0].get('uri', '')
            if not logo:
                logo = _img_url(p.get('picture') or p.get('logo'))
            # ETA: data.features.delivery.text
            eta = ''
            pdata = p.get('data') or {}
            feats = pdata.get('features') or {}
            if feats.get('delivery'):
                eta = _str_val(feats['delivery'].get('text'))
            places.append({
                'slug': slug,
                'name': name,
                'logo': _img_url(logo) if logo else '',
                'eta': eta or _place_eta(p),
                'business': p.get('business') or brand.get('business'),
            })
    # дедупликация по slug
    seen = set()
    out = []
    for pl in places:
        if pl.get('slug') and pl['slug'] not in seen:
            seen.add(pl['slug'])
            out.append(pl)
    return out


def _pick_text(obj):
    if isinstance(obj, dict):
        t = obj.get('text') or (obj.get('left_meta') or {}).get('text')
        if t:
            return t
        for k in ('money', 'tab', 'value'):
            v = obj.get(k)
            if isinstance(v, dict):
                r = _pick_text(v)
                if r:
                    return r
        for k, v in obj.items():
            if k.startswith('_'):  # noqa
                continue
            r = _pick_text(v)
            if r:
                return r
    return ''


def shop_info(account, slug, lat=None, lon=None):
    """Инфо о магазине/месте."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    body = {"slug": slug, "location": {"lat": lat, "lon": lon}, "shipping_types": ["delivery"]}
    return _dc_call(acc, 'POST', '/eats/v1/retail-catalog/v1/shop', json_body=body, lat=lat, lon=lon)


def menu_categories(account, slug, lat=None, lon=None):
    """Структура меню магазина (категории + товары) — get-categories."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    # список uid категорий
    cats = menu_goods_layout(acc, slug, lat, lon)
    uids = [c.get('uid') for c in cats]
    if not uids:
        return {'categories': []}
    # грузим товары батчами, чтобы не упереться в лимит
    batch = [{"uid": u, "min_items_count": 1, "max_items_count": 40} for u in uids]
    body = {"slug": slug, "categories": batch}
    return _dc_call(acc, 'POST', '/api/v2/menu/goods/get-categories', json_body=body, lat=lat, lon=lon)


def menu_goods_layout(acc, slug, lat, lon):
    """menu/goods с maxDepth=0 — только категории."""
    body = {"slug": slug, "latitude": lat, "longitude": lon, "maxDepth": 0, "filters": {}}
    d = _dc_call(acc, 'POST', '/api/v2/menu/goods', json_body=body, lat=lat, lon=lon)
    payload = d.get('payload') or d
    return payload.get('categories') or []


def full_cart(account, slug=None, screen='menu', lat=None, lon=None):
    """Полная корзина: позиции, суммы, промо."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    params = {
        'latitude': f'{lat}',
        'longitude': f'{lon}',
        'screen': screen,
        'shippingType': 'delivery',
        'with_move_cart_toast': 'true',
        'plus_subscription_toggle_state': 'false',
        'combo_subscription_toggle_state': 'false',
    }
    if slug:
        params['placeSlug'] = slug
    return _dc_call(acc, 'POST', '/eats/v1/cart/v2/full-carts', json_body={"need_items_icons": True},
                    params=params, lat=lat, lon=lon)


def _place_business_for(acc, slug):
    """Определить place_business для корзины (shop/restaurant)."""
    try:
        info = shop_info(acc, slug)
        return (info.get('place') or info.get('payload') or {}).get('business') or 'shop'
    except Exception:
        return 'shop'


def add_to_cart(account, slug, item_id, quantity=1, lat=None, lon=None):
    """Добавить/создать товар в корзине. Если корзины ещё нет — создаёт её.

    Возвращает {item_id (число для PUT), cart: {...}}.
    """
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    business = _place_business_for(acc, slug)
    body = {"item_id": item_id, "quantity": int(quantity), "item_options": [],
            "place_business": business, "place_slug": slug, "shipping_type": "delivery"}
    params = {
        'latitude': f'{lat}', 'longitude': f'{lon}', 'screen': 'menu',
        'shippingType': 'delivery', 'placeSlug': slug, 'soft_multi': 'true',
        'plus_subscription_toggle_state': 'false', 'combo_subscription_toggle_state': 'false',
    }
    return _dc_call(acc, 'POST', '/api/v1/cart', json_body=body, params=params, lat=lat, lon=lon)


def update_cart_item(account, slug, cart_item_id, item_id, quantity, lat=None, lon=None):
    """Изменить количество существующей позиции корзины (0 = удалить)."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    business = _place_business_for(acc, slug)
    body = {"item_id": item_id, "quantity": int(quantity), "item_options": [],
            "place_business": business, "place_slug": slug, "shipping_type": "delivery"}
    params = {
        'latitude': f'{lat}', 'longitude': f'{lon}', 'screen': 'menu',
        'shippingType': 'delivery', 'placeSlug': slug, 'soft_multi': 'true',
        'plus_subscription_toggle_state': 'false', 'combo_subscription_toggle_state': 'false',
    }
    return _dc_call(acc, 'PUT', f'/api/v1/cart/{cart_item_id}', json_body=body, params=params,
                    lat=lat, lon=lon)


def cart_promo_state(account, slug=None, lat=None, lon=None, screen='cart'):
    """Итог корзины (с учётом применённых промо)."""
    return full_cart(account, slug=slug, screen=screen, lat=lat, lon=lon)


def check_promo(account, lat=None, lon=None, cart_id=None):
    """Проверка доступных промокодов к корзине (promocodes/checkout)."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    cid = cart_id or _current_cart_uuid(acc)
    if not cid:
        raise RuntimeError('нет активной корзины')
    body = {"cart_id": cid, "receiving_type": "delivery"}
    return _dc_call(acc, 'POST', '/api/v1/user/promocodes/checkout', json_body=body,
                    lat=lat, lon=lon)


def _current_cart_uuid(acc, lat=None, lon=None):
    """Текущий uuid корзины из full-carts."""
    d = full_cart(acc, lat=lat, lon=lon)
    cart = d.get('cart') or {}
    return cart.get('id')


def apply_promo(account, code, slug=None, lat=None, lon=None, cart_id=None):
    """Применить промокод к корзине (api/v2/cart/promocode)."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    cid = cart_id or _current_cart_uuid(acc, lat, lon)
    params = {
        'placeSlug': slug or '', 'soft_multi': 'true', 'screen': 'checkout',
        'shippingType': 'delivery', 'receiving_type': 'delivery',
        'offer_identity': '', 'latitude': f'{lat}', 'longitude': f'{lon}',
    }
    body = {"code": str(code).strip()}
    return _dc_call(acc, 'POST', '/api/v2/cart/promocode', json_body=body, params=params,
                    lat=lat, lon=lon)


def remove_promo(account, offer_identity, slug=None, lat=None, lon=None):
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    params = {
        'placeSlug': slug or '', 'soft_multi': 'true', 'screen': 'checkout',
        'shippingType': 'delivery', 'receiving_type': 'delivery',
        'offer_identity': offer_identity or '', 'latitude': f'{lat}', 'longitude': f'{lon}',
    }
    return _dc_call(acc, 'DELETE', '/api/v2/cart/promocode', params=params, lat=lat, lon=lon)


def go_checkout(account, address, payment_id, place_slug, lat=None, lon=None):
    """Оформить: получить offers (способы оплаты, суммы, offer_identity)."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    lat = lat if lat is not None else float(acc.get('lat', DEFAULT_LAT))
    lon = lon if lon is not None else float(acc.get('lon', DEFAULT_LON))
    body = {
        "address": address,
        "place_slug": place_slug,
        "payment": {"recently_link_cards": False,
                    "selected_payment_type": {"id": payment_id, "type": "card"}},
    }
    return _dc_call(acc, 'POST', '/api/v2/cart/go-checkout', json_body=body, lat=lat, lon=lon)


def create_order(account, order_payload, lat=None, lon=None):
    """Создать заказ (POST /api/v1/orders)."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    return _dc_call(acc, 'POST', '/api/v1/orders', json_body=order_payload, lat=lat, lon=lon)


def tracking(account, order_id, lat=None, lon=None):
    """Статус платежа/заказа."""
    acc = get_delivery_account(account) if isinstance(account, str) else account
    body = {"order_id": order_id}
    return _dc_call(acc, 'POST', '/eats/v1/eats-payments/v1/order/tracking', json_body=body,
                    lat=lat, lon=lon)


def build_address(addr):
    """Собрать структуру address для go-checkout / orders из словаря.

    addr = {city, street, house, entrance, doorcode, floor, office,
            comment, full_text, uri, areas:[...]}
    """
    loc = addr.get('location') or [addr.get('lon'), addr.get('lat')]
    if (not isinstance(loc, list) or len(loc) < 2
            or loc[0] is None or loc[1] is None):
        loc = [DEFAULT_LON, DEFAULT_LAT]
    details = []
    for t in ('doorcode', 'office', 'entrance', 'floor'):
        v = addr.get(t)
        if v:
            details.append({"type": t, "text": str(v)})
    uri = addr.get('uri') or DEFAULT_URI
    base = {
        "city": addr.get('city', ''),
        "street": addr.get('street', ''),
        "house": addr.get('house', ''),
        "country": addr.get('country', 'Россия'),
        "areas": addr.get('areas') or ["городской округ Омск"],
        "location": loc,
        "is_pickup_point": False,
        "uri": uri,
    }
    for f in ('entrance', 'doorcode', 'floor', 'office'):
        if addr.get(f):
            base[f] = str(addr[f])
    if addr.get('comment'):
        base['comment'] = str(addr['comment'])
    full_text = addr.get('full_text') or f"{addr.get('city','')}, {addr.get('street','')}, {addr.get('house','')}".strip(', ')
    base['full_text'] = full_text
    return {"base_info": base, "details": details}


# ---------- broadcast-утилиты ----------
def _first(b):  # noqa
    pass


# Вызывается при импорте (seed дефолтного аккаунта, если файла ещё нет).
def ensure_default_account():
    if not os.path.exists(DELIVERY_ACCOUNTS_FILE):
        accs = load_delivery_accounts()
        if not accs:
            accs.append({
                'name': 'delivery_live',
                'lat': DEFAULT_LAT,
                'lon': DEFAULT_LON,
                'creds': {
                    'authorization': _DEFAULT_BEARER(),
                    'cookie': _DEFAULT_COOKIE(),
                    'x_yandex_uid': '2332872052',
                    'x_device_id': '2fe26c2d-00b9-3589-bd16-e0c0581a9799',
                    'x_appmetrica_deviceid': '18af25b608d993be5a06e7661b2f8b55',
                    'x_appmetrica_uuid': '312f632728ac4e15926bc3f31c9423f8',
                    'x_client_session': '039c596a-b518-4058-8e81-82342c0a270f',
                    'x_tracker_id': 'b5cb8b44-0c04-427a-adb8-667c0665d3bb',
                    'x_mob_id': 'b81981339c54492f8b32e3e0ba625f90',
                },
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            })
            save_delivery_accounts(accs)


_DEFAULT_BEARER_CACHE = None


def _DEFAULT_BEARER():
    global _DEFAULT_BEARER_CACHE
    if _DEFAULT_BEARER_CACHE:
        return _DEFAULT_BEARER_CACHE
    # Значение из живой захваченной сессии делливери.
    _DEFAULT_BEARER_CACHE = ('Bearer '
        '2.2332872052.554114.1819982075.1788446075813.1.0.12159080.'
        'Lv4me27ywErptPPt.wrfC86oao8DAxOLVfHXJhxmKBNKjI3Xf2AUnVfmaE8Tix47OT0TTexOxLQ69Rmn-'
        '5BVJHL7Grc2hNxjvjQQwOlaXFT6E-rrA7mfdetVZ-OfZrhQ0YXIKEY9rDW4O.PlTnmsbzYBHXICvkZHqjUQ')
    return _DEFAULT_BEARER_CACHE


def _DEFAULT_COOKIE():
    return 'Eats-Session=61d8f4bc01c74adda5d32f86b033bcfc'


# ---------- добавление аккаунта по QR (как у Я.Еды) ----------
# Полный аналог eda.qr_start/eda.qr_status, но результат сохраняется в
# аккаунты Делливери (bearer-токен получается через exchange_sessionid).
import threading as _threading

_DL_QR_LOCK = _threading.Lock()
_DL_QR_TTL = 600
_DL_PASSPORT_PWL = 'https://passport.yandex.ru/pwl-yandex'
_DL_QR_FILE = os.path.join(core.DATA_DIR, 'delivery_qr_state.json')


def _dl_qr_load_state():
    return _read_json(_DL_QR_FILE)


def _dl_qr_save_state(state):
    _write_json_atomic(_DL_QR_FILE, state)


def _dl_qr_cookies_to_dict(session):
    return requests.utils.dict_from_cookiejar(session.cookies) if hasattr(requests, 'utils') else {c.name: c.value for c in session.cookies}


def _dl_qr_rebuild_session(cookies_dict):
    s = requests.Session()
    if cookies_dict:
        cj = requests.utils.cookiejar_from_dict(cookies_dict)
        s.cookies.update(cj)
    return s


def _dl_qr_headers(csrf):
    return {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRF-Token': csrf,
    }


def _dl_new_device_headers():
    """Сгенерировать набор app/device-заголовков, которые требуются dc.eda.yandex.net.

    Без X-Device-Id API отдаёт 400 "Missing or empty 'X-Device-Id' header".
    """
    def u():
        return str(uuid.uuid4())
    return {
        'x_device_id': u(),
        'x_appmetrica_deviceid': u(),
        'x_appmetrica_uuid': u(),
        'x_client_session': u(),
        'x_tracker_id': u(),
        'x_mob_id': u(),
    }


def delivery_qr_start(account_name=''):
    """Создать QR-сессию входа Я.Еды/Делливери. Возвращает (qr_id, link).

    Состояние (cookies + track_id + csrf) хранится в файле, поэтому
    переживает перезапуски сервера/replic и multi-worker.
    При подтверждении delivery_qr_status обменяет Session_id на OAuth-токен
    и сохранит аккаунт Делливери.
    """
    s = requests.Session()
    try:
        r = s.get(_DL_PASSPORT_PWL, headers=_dl_qr_headers(''), timeout=25)
        r.raise_for_status()
        m = re.search(r'__CSRF__\s*=\s*"([^"]+)"', r.text)
        if not m:
            raise RuntimeError('passport: CSRF не найден в странице')
        csrf = m.group(1)
        h = _dl_qr_headers(csrf)
        r = s.post(_DL_PASSPORT_PWL + '/api/passport/auth/password/submit',
                   headers=h, data=json.dumps({'retpath': 'https://passport.yandex.ru/'}), timeout=25)
        r.raise_for_status()
        magic = r.json()
        track_id = magic.get('track_id') or ''
        csrf_token = magic.get('csrf_token') or ''
        if not track_id:
            raise RuntimeError('passport: нет track_id: ' + r.text[:200])
        r = s.post(_DL_PASSPORT_PWL + '/api/passport/auth/magic/code',
                   headers=h,
                   data=json.dumps({'location_id': '0', 'magic_track_id': track_id, 'track_id': ''}),
                   timeout=25)
        r.raise_for_status()
        link = r.json().get('link') or ''
        if not link:
            raise RuntimeError('passport: нет link: ' + r.text[:200])
    except requests.RequestException as e:
        raise RuntimeError(f'passport: сеть: {e}')
    qr_id = uuid.uuid4().hex
    st = {
        'cookies': _dl_qr_cookies_to_dict(s),
        'csrf': csrf, 'magic_track_id': track_id,
        'csrf_token': csrf_token, 'link': link, 'created_at': time.time(),
        'account_name': (account_name or '').strip(),
    }
    with _DL_QR_LOCK:
        state = _dl_qr_load_state()
        state[qr_id] = st
        _dl_qr_save_state(state)
    return qr_id, link


def delivery_qr_status(qr_id):
    """Поллинг статуса QR-входа Делливери.

    Возвращает {'state': 'waiting'|'ok'|'expired'|'error', ...}. При 'ok'
    аккаунт уже создан/обновлён с bearer-токеном (authorization), cookie и uid.
    """
    import eda as _eda
    with _DL_QR_LOCK:
        st = _dl_qr_load_state().get(qr_id)
    if not st:
        return {'state': 'error', 'message': 'сессия не найдена (сервер перезапущен?)'}
    if st.get('done'):
        return {'state': 'ok', 'account': st.get('account'), 'bearer': st.get('bearer')}
    if time.time() - st.get('created_at', 0) > _DL_QR_TTL:
        with _DL_QR_LOCK:
            state = _dl_qr_load_state()
            state.pop(qr_id, None)
            _dl_qr_save_state(state)
        return {'state': 'expired', 'message': 'ссылка устарела — создайте новую'}
    s = _dl_qr_rebuild_session(st.get('cookies') or {})
    h = _dl_qr_headers(st.get('csrf', ''))
    try:
        r = s.post(_DL_PASSPORT_PWL + '/api/passport/auth/magic/code/status',
                   headers=h, data=json.dumps({
                       'track_id': st.get('magic_track_id'), 'csrf_token': st.get('csrf_token'),
                       'yandexAllowedDomains': []}), timeout=25)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        return {'state': 'error', 'message': f'поллинг: {e}'}
    state = d.get('state')
    if state in (None, 'otp_auth_not_ready'):
        return {'state': 'waiting'}
    if state == 'auth_challenge':
        return {'state': 'waiting', 'hint': 'нужно доп. подтверждение в Яндекс-приложении'}
    if state != 'otp_auth_finished':
        return {'state': 'waiting', 'hint': f'state={state}'}
    track_id = d.get('trackId')
    if not track_id:
        return {'state': 'error', 'message': f'нет trackId: {d}'}
    try:
        r = s.post(_DL_PASSPORT_PWL + '/api/passport/sessions/get_session',
                   headers=h, data=json.dumps({'track_id': track_id}), timeout=25)
        r.raise_for_status()
    except requests.RequestException as e:
        return {'state': 'error', 'message': f'get_session: {e}'}
    ck = {c.name: c.value for c in s.cookies}
    session_id = ck.get('Session_id') or ''
    if not session_id:
        return {'state': 'error', 'message': f'нет Session_id в cookies: {ck}'}
    # Обмениваем Session_id на OAuth Bearer-токен (тот же путь, что у Я.Еды).
    try:
        bearer, uid = _eda.exchange_sessionid(session_id)
    except Exception as e:
        return {'state': 'error', 'message': f'обмен Session_id на токен: {e}'}
    bearer = bearer if bearer.startswith('Bearer ') else 'Bearer ' + bearer
    name = st.get('account_name', '')
    # cookie: берём Eats-Session из захваченных cookies, если есть; иначе Session_id.
    eats_session = ck.get('Eats-Session') or ck.get('Session_id') or session_id
    cookie = 'Eats-Session=' + eats_session
    dev = _dl_new_device_headers()
    try:
        accs = load_delivery_accounts()
        target = None
        if name:
            for a in accs:
                if a.get('name') == name:
                    target = a
                    break
        if target is None:
            for a in accs:
                cc = a.get('creds') or {}
                if cc.get('cookie') == cookie:
                    target = a
                    break
        if target is None:
            target = {
                'name': name or ('delivery_' + session_id[:8]),
                'lat': DEFAULT_LAT, 'lon': DEFAULT_LON,
                'creds': dict(dev, authorization=bearer, cookie=cookie,
                              x_yandex_uid=uid if uid else ck.get('yandexuid', '')),
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            }
            accs.append(target)
        else:
            cc = target.setdefault('creds', {})
            for k, v in dev.items():
                cc.setdefault(k, v)
            cc['authorization'] = bearer
            cc['cookie'] = cookie
            if uid:
                cc['x_yandex_uid'] = uid
            elif ck.get('yandexuid'):
                cc['x_yandex_uid'] = ck['yandexuid']
        save_delivery_accounts(accs)
    except Exception as e:
        return {'state': 'error', 'message': f'сохранение аккаунта: {e}'}
    with _DL_QR_LOCK:
        state = _dl_qr_load_state()
        state[qr_id] = {'done': True, 'account': target.get('name'),
                        'bearer': bearer[:20] + '…', 'created_at': st.get('created_at', time.time())}
        _dl_qr_save_state(state)
    return {'state': 'ok', 'account': target.get('name'), 'bearer': bearer[:20] + '…'}


ensure_default_account()
