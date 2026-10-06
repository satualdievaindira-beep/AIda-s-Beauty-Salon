#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aida's Beauty Salon: сайт салона красоты (Бишкек).
Flask + SQLite, два языка (русский и кыргызский), онлайн-запись, админка.

Запуск:
    pip install flask
    python app.py
Сайт:    http://127.0.0.1:5000
Админка: http://127.0.0.1:5000/admin
         (пароль берётся из переменной ADMIN_PASSWORD, по умолчанию "admin123":
          обязательно смените перед публикацией)
"""

import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import quote, urlparse

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import seed_data
from i18n import DEFAULT_LANG, LANG_LABELS, LANGS, format_date, format_price, translate

# ---------------------------------------------------------------------------
# НАСТРОЙКИ САЛОНА: здесь меняются контакты, ссылки и режим работы
# ---------------------------------------------------------------------------
SHOP_NAME = "Aida's Beauty Salon"

# Ссылка на логотип. Вставьте сюда адрес картинки из интернета.
# Если оставить пустым, берётся файл static/img/logo.png из проекта.
LOGO_URL = ""

# Ссылка на точку салона в 2ГИС (геолокация)
MAP_URL = "https://go.2gis.com/DrZYd"

# WhatsApp заведующей: только цифры, с кодом страны, без плюса
WHATSAPP_DIGITS = "996707324777"
PHONE_DISPLAY = "+996 707 324 777"

CITY = "Бишкек"

# Режим работы: день недели (0 = понедельник) -> (открытие, закрытие) в часах.
# Значения примерные: укажите реальные часы салона.
WORK_HOURS = {d: (10, 20) for d in range(7)}

SLOT_STEP = 30           # шаг записи, минут
LEAD_MINUTES = 60        # запись возможна не позднее чем за час до визита
BOOK_DAYS_AHEAD = 30     # на сколько дней вперёд открыта запись
CANCEL_BEFORE_MIN = 120  # отмена возможна не позднее чем за 2 часа

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "aida.db")

BOOKING_STATUSES = ("new", "confirmed", "done", "no_show", "cancelled")
ACTIVE_STATUSES = ("new", "confirmed")
STATUS_RU = {"new": "Новая", "confirmed": "Подтверждена", "done": "Выполнена",
             "no_show": "Не пришли", "cancelled": "Отменена"}

ADMIN_HASH = generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=64 * 1024,
)
ASSET_VERSION = int(time.time())

# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT    NOT NULL,
    name_ru     TEXT    NOT NULL,
    name_ky     TEXT    NOT NULL DEFAULT '',
    desc_ru     TEXT    NOT NULL DEFAULT '',
    desc_ky     TEXT    NOT NULL DEFAULT '',
    price       INTEGER NOT NULL CHECK (price >= 0),
    price_from  INTEGER NOT NULL DEFAULT 0,
    duration    INTEGER NOT NULL CHECK (duration BETWEEN 15 AND 480),
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS masters (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT    NOT NULL,
    name_ru  TEXT    NOT NULL,
    name_ky  TEXT    NOT NULL DEFAULT '',
    active   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS bookings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,
    service_id INTEGER NOT NULL REFERENCES services(id),
    master_id  INTEGER NOT NULL REFERENCES masters(id),
    name       TEXT    NOT NULL,
    phone      TEXT    NOT NULL,
    day        TEXT    NOT NULL,
    start_min  INTEGER NOT NULL,
    end_min    INTEGER NOT NULL,
    price      INTEGER NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'new',
    lang       TEXT    NOT NULL DEFAULT 'ru',
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_day ON bookings(day, master_id);
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    rating     INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    body       TEXT NOT NULL,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def get_db():
    if "db" not in g:
        # isolation_level=None: транзакциями управляем сами (BEGIN IMMEDIATE)
        g.db = sqlite3.connect(DATABASE, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    rows = get_db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    return get_db().execute(sql, args).lastrowid


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO services (category, name_ru, name_ky, desc_ru, desc_ky, price, price_from, duration) "
            "VALUES (?,?,?,?,?,?,?,?)", seed_data.SERVICES)
        db.executemany("INSERT INTO masters (category, name_ru, name_ky) VALUES (?,?,?)", seed_data.MASTERS)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Язык интерфейса
# ---------------------------------------------------------------------------
@app.before_request
def pick_language():
    lang = session.get("lang")
    if lang not in LANGS:
        lang = request.accept_languages.best_match(list(LANGS), default=DEFAULT_LANG)
    g.lang = lang


def t(key, **kwargs):
    return translate(g.lang, key, **kwargs)


def loc(row, field):
    """Поле строки БД на текущем языке (name -> name_ru / name_ky) с запасным русским."""
    value = row[f"{field}_{g.lang}"] if f"{field}_{g.lang}" in row.keys() else ""
    return value or row[f"{field}_ru"]


def category_info(key):
    for k, ru, ky, d_ru, d_ky in seed_data.CATEGORIES:
        if k == key:
            return {"key": k, "name": ky if g.lang == "ky" else ru, "desc": d_ky if g.lang == "ky" else d_ru}
    return {"key": key, "name": key, "desc": ""}


def categories():
    return [category_info(c[0]) for c in seed_data.CATEGORIES]


@app.route("/lang/<code>")
def set_language(code):
    if code in LANGS:
        session["lang"] = code
        session.permanent = True
    target = request.referrer or url_for("index")
    parsed = urlparse(target)
    if parsed.netloc and parsed.netloc != request.host:  # защита от внешних редиректов
        target = url_for("index")
    return redirect(target)


# ---------------------------------------------------------------------------
# Безопасность и вспомогательные функции
# ---------------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


@app.before_request
def protect_from_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, description="csrf")


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data: https:; "
        "connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    return resp


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


_hits = defaultdict(deque)


def rate_limited(key, limit, window):
    """Ограничитель в памяти: не более `limit` событий за `window` секунд."""
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def client_ip():
    return request.remote_addr or "unknown"


def clean(text, limit):
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def clean_multiline(text, limit):
    return (text or "").strip().replace("\r", "")[:limit]


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,17}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def valid_phone(v):
    return bool(PHONE_RE.match(v)) and len(re.sub(r"\D", "", v)) >= 7


def phone_digits(v):
    return re.sub(r"\D", "", v or "")


def to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def fmt_min(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def day_in_range(d):
    today = date.today()
    return d is not None and today <= d <= today + timedelta(days=BOOK_DAYS_AHEAD)


def hours_text(d=None):
    d = d or date.today()
    open_h, close_h = WORK_HOURS[d.weekday()]
    return f"{open_h}:00-{close_h}:00"


def new_code():
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if not query("SELECT 1 FROM bookings WHERE code = ?", (code,), one=True):
            return code


def whatsapp_link(text=""):
    base = f"https://wa.me/{WHATSAPP_DIGITS}"
    return f"{base}?text={quote(text)}" if text else base


# ---------------------------------------------------------------------------
# Расписание: свободные окна
# ---------------------------------------------------------------------------
def busy_intervals(day_iso, master_id):
    rows = query(
        "SELECT start_min, end_min FROM bookings WHERE day = ? AND master_id = ? "
        "AND status IN ('new', 'confirmed')", (day_iso, master_id))
    return [(r["start_min"], r["end_min"]) for r in rows]


def master_is_free(day_iso, master_id, start, end):
    return all(end <= s or start >= e for s, e in busy_intervals(day_iso, master_id))


def candidate_masters(service, master_id):
    """Мастера, которые могут выполнить услугу. master_id == 0 означает 'любой'."""
    if master_id:
        row = query("SELECT id FROM masters WHERE id = ? AND category = ? AND active = 1",
                    (master_id, service["category"]), one=True)
        return [row["id"]] if row else []
    rows = query("SELECT id FROM masters WHERE category = ? AND active = 1 ORDER BY id", (service["category"],))
    return [r["id"] for r in rows]


def free_slots(day, master_id, service):
    """Начала свободных окон в минутах от полуночи."""
    open_h, close_h = WORK_HOURS[day.weekday()]
    open_m, close_m = open_h * 60, close_h * 60
    earliest = open_m
    now = datetime.now()
    if day == now.date():
        raw = now.hour * 60 + now.minute + LEAD_MINUTES
        earliest = max(open_m, -(-raw // SLOT_STEP) * SLOT_STEP)
    result = set()
    for mid in candidate_masters(service, master_id):
        busy = busy_intervals(day.isoformat(), mid)
        start = open_m
        while start + service["duration"] <= close_m:
            end = start + service["duration"]
            if start >= earliest and all(end <= s or start >= e for s, e in busy):
                result.add(start)
            start += SLOT_STEP
    return sorted(result)


def create_booking(service, master_id, day, start, name, phone, note, lang):
    """Создаёт запись под защитой транзакции. Возвращает (код, id мастера) или None."""
    db = get_db()
    end = start + service["duration"]
    db.execute("BEGIN IMMEDIATE")
    try:
        chosen = next((m for m in candidate_masters(service, master_id)
                       if master_is_free(day.isoformat(), m, start, end)), None)
        if chosen is None:
            db.execute("ROLLBACK")
            return None
        code = new_code()
        db.execute(
            "INSERT INTO bookings (code, service_id, master_id, name, phone, day, start_min, end_min, "
            "price, note, lang, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, service["id"], chosen, name, phone, day.isoformat(), start, end,
             service["price"], note, lang, now_str()))
        db.execute("COMMIT")
        return code, chosen
    except Exception:
        db.execute("ROLLBACK")
        raise


def booking_with_details(code):
    return query(
        "SELECT b.*, s.name_ru AS service_ru, s.name_ky AS service_ky, s.duration, "
        "m.name_ru AS master_ru, m.name_ky AS master_ky "
        "FROM bookings b JOIN services s ON s.id = b.service_id "
        "JOIN masters m ON m.id = b.master_id WHERE b.code = ?", (code,), one=True)


def can_cancel(booking):
    if booking["status"] not in ACTIVE_STATUSES:
        return False
    visit = datetime.combine(parse_day(booking["day"]), datetime.min.time()) + timedelta(minutes=booking["start_min"])
    return visit - datetime.now() >= timedelta(minutes=CANCEL_BEFORE_MIN)


# ---------------------------------------------------------------------------
# Jinja: функции и переменные для шаблонов
# ---------------------------------------------------------------------------
@app.template_filter("hm")
def filter_hm(minutes):
    return fmt_min(int(minutes))


@app.template_filter("ldate")
def filter_ldate(value):
    d = parse_day(value) if isinstance(value, str) else value
    return format_date(g.lang, d) if d else value


@app.context_processor
def inject_globals():
    today = date.today()
    open_h, close_h = WORK_HOURS[today.weekday()]
    return {
        "t": t,
        "L": loc,
        "lang": g.lang,
        "langs": [(code, LANG_LABELS[code]) for code in LANGS],
        "site": {
            "name": SHOP_NAME,
            "city": CITY,
            "map_url": MAP_URL,
            "logo_url": LOGO_URL or url_for("static", filename="img/logo.png"),
            "phone": PHONE_DISPLAY,
            "tel": "tel:+" + WHATSAPP_DIGITS,
            "wa": whatsapp_link(t("wa.hello")),
            "hours": t("contact.hours_value", open=f"{open_h}:00", close=f"{close_h}:00"),
        },
        "price_label": lambda amount, is_from=False: format_price(g.lang, amount, bool(is_from)),
        "category_info": category_info,
        "csrf_token": csrf_token,
        "is_admin": session.get("is_admin", False),
        "asset_v": ASSET_VERSION,
        "year": today.year,
    }


# ---------------------------------------------------------------------------
# Публичные страницы
# ---------------------------------------------------------------------------
def services_by_category():
    grouped = []
    for cat in categories():
        rows = query("SELECT * FROM services WHERE category = ? AND active = 1 ORDER BY price, id", (cat["key"],))
        cheapest = min((r["price"] for r in rows), default=0)
        grouped.append({"cat": cat, "rows": rows, "min_price": cheapest})
    return grouped


@app.route("/")
def index():
    return render_template(
        "index.html",
        groups=services_by_category(),
        reviews=query("SELECT * FROM reviews ORDER BY id DESC LIMIT 3"),
        stats=query("SELECT ROUND(AVG(rating), 1) AS avg, COUNT(*) AS cnt FROM reviews", one=True),
    )


@app.route("/services")
def services():
    return render_template("services.html", groups=services_by_category())


@app.route("/booking", methods=["GET", "POST"])
def booking():
    form = {
        "service": to_int(request.args.get("service")),
        "master": to_int(request.args.get("master")),
        "day": request.args.get("day", ""),
        "time": "", "name": "", "phone": "", "note": "",
    }
    if request.method == "POST":
        form.update({
            "service": to_int(request.form.get("service")),
            "master": to_int(request.form.get("master")),
            "day": clean(request.form.get("day"), 10),
            "time": clean(request.form.get("time"), 5),
            "name": clean(request.form.get("name"), 80),
            "phone": clean(request.form.get("phone"), 20),
            "note": clean_multiline(request.form.get("note"), 300),
        })
        if request.form.get("website"):  # ловушка для ботов
            abort(400)
        errors = []
        service = query("SELECT * FROM services WHERE id = ? AND active = 1", (form["service"],), one=True)
        if not service:
            errors.append(t("err.service"))
        elif form["master"] and not candidate_masters(service, form["master"]):
            errors.append(t("err.master"))
        elif not candidate_masters(service, 0):
            errors.append(t("book.no_master"))
        day = parse_day(form["day"])
        if not day_in_range(day):
            errors.append(t("err.date", n=BOOK_DAYS_AHEAD))
        if not TIME_RE.match(form["time"]):
            errors.append(t("err.time"))
        if len(form["name"]) < 2:
            errors.append(t("err.name"))
        if not valid_phone(form["phone"]):
            errors.append(t("err.phone"))
        if not errors and rate_limited(("book", client_ip()), 6, 3600):
            errors.append(t("err.rate"))
        if not errors:
            start = to_min(form["time"])
            if start not in free_slots(day, form["master"], service):
                errors.append(t("err.busy"))
            else:
                result = create_booking(service, form["master"], day, start, form["name"],
                                        form["phone"], form["note"], g.lang)
                if result is None:
                    errors.append(t("err.taken"))
                else:
                    session.setdefault("my_codes", []).append(result[0])
                    session.modified = True
                    return redirect(url_for("booking_done", code=result[0]))
        for e in errors:
            flash(e, "error")
    slots = []
    service = query("SELECT * FROM services WHERE id = ?", (form["service"],), one=True)
    day = parse_day(form["day"])
    if service and day_in_range(day):
        slots = [fmt_min(m) for m in free_slots(day, form["master"], service)]
    today = date.today()
    return render_template(
        "booking.html", groups=services_by_category(), form=form, slots=slots,
        masters=query("SELECT * FROM masters WHERE active = 1 ORDER BY category, id"),
        min_day=today.isoformat(), max_day=(today + timedelta(days=BOOK_DAYS_AHEAD)).isoformat(),
    )


@app.route("/booking/<code>")
def booking_done(code):
    if code not in session.get("my_codes", []):
        abort(404)
    item = booking_with_details(code)
    if not item:
        abort(404)
    service_name = item["service_ky"] if g.lang == "ky" and item["service_ky"] else item["service_ru"]
    message = t("wa.booking", service=service_name, date=format_date(g.lang, parse_day(item["day"])),
                time=fmt_min(item["start_min"]), code=item["code"])
    return render_template("booking_done.html", b=item, wa_text_link=whatsapp_link(message))


@app.route("/lookup", methods=["GET", "POST"])
def lookup():
    item = None
    if request.method == "POST":
        code = clean(request.form.get("code"), 6).upper()
        phone = phone_digits(request.form.get("phone"))
        if rate_limited(("lookup", client_ip()), 10, 600):
            flash(t("lookup.too_many"), "error")
        else:
            found = booking_with_details(code)
            if found and len(phone) >= 6 and phone_digits(found["phone"]).endswith(phone[-6:]):
                item = found
                session["lookup"] = {"code": code, "tail": phone[-6:]}
            else:
                flash(t("lookup.not_found"), "error")
    return render_template("lookup.html", b=item, cancellable=bool(item) and can_cancel(item))


@app.route("/lookup/cancel", methods=["POST"])
def lookup_cancel():
    info = session.get("lookup") or {}
    item = booking_with_details(info.get("code", ""))
    if not item or not phone_digits(item["phone"]).endswith(info.get("tail", "x")):
        abort(404)
    if can_cancel(item):
        execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (item["id"],))
        flash(t("lookup.cancelled"), "ok")
    else:
        flash(t("lookup.cancel_late", h=CANCEL_BEFORE_MIN // 60), "error")
    return redirect(url_for("lookup"))


@app.route("/reviews", methods=["GET", "POST"])
def reviews():
    form = {}
    if request.method == "POST":
        form = {"name": clean(request.form.get("name"), 60), "rating": to_int(request.form.get("rating")),
                "body": clean_multiline(request.form.get("body"), 600)}
        if len(form["name"]) < 2 or not 1 <= form["rating"] <= 5 or len(form["body"]) < 5:
            flash(t("reviews.error"), "error")
        elif rate_limited(("review", client_ip()), 3, 3600):
            flash(t("reviews.rate"), "error")
        else:
            execute("INSERT INTO reviews (name, rating, body, created_at) VALUES (?,?,?,?)",
                    (form["name"], form["rating"], form["body"], now_str()))
            flash(t("reviews.thanks"), "ok")
            return redirect(url_for("reviews"))
    return render_template(
        "reviews.html", form=form,
        items=query("SELECT * FROM reviews ORDER BY id DESC LIMIT 50"),
        stats=query("SELECT ROUND(AVG(rating), 1) AS avg, COUNT(*) AS cnt FROM reviews", one=True))


@app.route("/contact", methods=["GET", "POST"])
def contact():
    form = {}
    if request.method == "POST":
        form = {"name": clean(request.form.get("name"), 80), "email": clean(request.form.get("email"), 120),
                "body": clean_multiline(request.form.get("body"), 2000)}
        if len(form["name"]) < 2 or not EMAIL_RE.match(form["email"]) or len(form["body"]) < 10:
            flash(t("contact.error"), "error")
        elif rate_limited(("msg", client_ip()), 4, 3600):
            flash(t("contact.rate"), "error")
        else:
            execute("INSERT INTO messages (name, email, body, created_at) VALUES (?,?,?,?)",
                    (form["name"], form["email"], form["body"], now_str()))
            flash(t("contact.thanks"), "ok")
            return redirect(url_for("contact"))
    return render_template("contact.html", form=form)


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="img/logo.png"), code=302)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.route("/api/slots")
def api_slots():
    service = query("SELECT * FROM services WHERE id = ? AND active = 1", (to_int(request.args.get("service")),), one=True)
    day = parse_day(request.args.get("day"))
    if not service or not day_in_range(day):
        return jsonify(ok=False, error=t("err.api")), 400
    master_id = to_int(request.args.get("master"))
    if not candidate_masters(service, master_id):
        return jsonify(ok=False, error=t("book.no_master")), 400
    slots = [fmt_min(m) for m in free_slots(day, master_id, service)]
    return jsonify(ok=True, slots=slots, hours=hours_text(day), duration=service["duration"])


@app.route("/api/services")
def api_services():
    rows = query("SELECT id, category, name_ru, name_ky, price, price_from, duration FROM services WHERE active = 1")
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Админ-панель (только на русском)
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if rate_limited(("login", client_ip()), 5, 600):
            flash("Слишком много попыток входа. Подождите 10 минут.", "error")
        elif check_password_hash(ADMIN_HASH, request.form.get("password", "")):
            session["is_admin"] = True
            session.permanent = True
            target = request.args.get("next", "")
            return redirect(target if target.startswith("/admin") else url_for("admin_dashboard"))
        else:
            flash("Неверный пароль.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    stats = {
        "today": query("SELECT COUNT(*) c FROM bookings WHERE day = ? AND status IN ('new','confirmed','done')", (today,), one=True)["c"],
        "new": query("SELECT COUNT(*) c FROM bookings WHERE status = 'new'", one=True)["c"],
        "revenue": query("SELECT COALESCE(SUM(price), 0) s FROM bookings WHERE status = 'done' AND day >= ?", (week_ago,), one=True)["s"],
        "unread": query("SELECT COUNT(*) c FROM messages WHERE is_read = 0", one=True)["c"],
    }
    schedule = query(
        "SELECT b.*, s.name_ru AS service_name, m.name_ru AS master_name FROM bookings b "
        "JOIN services s ON s.id = b.service_id JOIN masters m ON m.id = b.master_id "
        "WHERE b.day = ? AND b.status IN ('new','confirmed','done') ORDER BY b.start_min", (today,))
    top = query(
        "SELECT s.name_ru AS name, COUNT(*) AS cnt FROM bookings b JOIN services s ON s.id = b.service_id "
        "WHERE b.status != 'cancelled' GROUP BY s.id ORDER BY cnt DESC LIMIT 5")
    return render_template("admin/dashboard.html", stats=stats, schedule=schedule, top=top, statuses=STATUS_RU)


@app.route("/admin/bookings", methods=["GET", "POST"])
@admin_required
def admin_bookings():
    if request.method == "POST":
        status = request.form.get("status", "")
        if status in BOOKING_STATUSES:
            execute("UPDATE bookings SET status = ? WHERE id = ?", (status, to_int(request.form.get("booking_id"))))
            flash("Статус записи обновлён.", "ok")
        return redirect(url_for("admin_bookings", day=request.args.get("day", ""), status=request.args.get("status", "")))
    day, status = request.args.get("day", ""), request.args.get("status", "")
    sql = ("SELECT b.*, s.name_ru AS service_name, m.name_ru AS master_name FROM bookings b "
           "JOIN services s ON s.id = b.service_id JOIN masters m ON m.id = b.master_id WHERE 1=1")
    args = []
    if parse_day(day):
        sql += " AND b.day = ?"
        args.append(day)
    if status in BOOKING_STATUSES:
        sql += " AND b.status = ?"
        args.append(status)
    sql += " ORDER BY b.day DESC, b.start_min LIMIT 200"
    return render_template("admin/bookings.html", rows=query(sql, args), day=day, status=status, statuses=STATUS_RU)


@app.route("/admin/services", methods=["GET", "POST"])
@admin_required
def admin_services():
    if request.method == "POST":
        action, sid = request.form.get("action"), to_int(request.form.get("service_id"))
        if action == "add":
            cat = request.form.get("category", "")
            name_ru = clean(request.form.get("name_ru"), 80)
            price, duration = to_int(request.form.get("price"), -1), to_int(request.form.get("duration"))
            if cat not in seed_data.CATEGORY_KEYS or len(name_ru) < 2 or price < 0 or not 15 <= duration <= 480:
                flash("Проверьте категорию, название, цену и длительность (15-480 минут).", "error")
            else:
                execute("INSERT INTO services (category, name_ru, name_ky, desc_ru, desc_ky, price, price_from, duration) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (cat, name_ru, clean(request.form.get("name_ky"), 80), clean(request.form.get("desc_ru"), 300),
                         clean(request.form.get("desc_ky"), 300), price, 1 if request.form.get("price_from") else 0, duration))
                flash("Услуга добавлена.", "ok")
        elif action == "update":
            price, duration = to_int(request.form.get("price"), -1), to_int(request.form.get("duration"))
            if price >= 0 and 15 <= duration <= 480:
                execute("UPDATE services SET price = ?, duration = ?, price_from = ? WHERE id = ?",
                        (price, duration, 1 if request.form.get("price_from") else 0, sid))
                flash("Услуга обновлена.", "ok")
        elif action == "toggle":
            execute("UPDATE services SET active = 1 - active WHERE id = ?", (sid,))
        elif action == "delete":
            if query("SELECT 1 FROM bookings WHERE service_id = ?", (sid,), one=True):
                flash("У услуги есть записи. Скройте её вместо удаления.", "error")
            else:
                execute("DELETE FROM services WHERE id = ?", (sid,))
                flash("Услуга удалена.", "ok")
        return redirect(url_for("admin_services"))
    rows = query("SELECT * FROM services ORDER BY category, active DESC, price")
    return render_template("admin/services.html", rows=rows, cats=seed_data.CATEGORIES)


@app.route("/admin/masters", methods=["GET", "POST"])
@admin_required
def admin_masters():
    if request.method == "POST":
        action, mid = request.form.get("action"), to_int(request.form.get("master_id"))
        if action == "add":
            cat, name_ru = request.form.get("category", ""), clean(request.form.get("name_ru"), 60)
            if cat not in seed_data.CATEGORY_KEYS or len(name_ru) < 2:
                flash("Выберите направление и укажите имя мастера.", "error")
            else:
                execute("INSERT INTO masters (category, name_ru, name_ky) VALUES (?,?,?)",
                        (cat, name_ru, clean(request.form.get("name_ky"), 60) or name_ru))
                flash("Мастер добавлен.", "ok")
        elif action == "rename":
            name_ru = clean(request.form.get("name_ru"), 60)
            if len(name_ru) >= 2:
                execute("UPDATE masters SET name_ru = ?, name_ky = ? WHERE id = ?",
                        (name_ru, clean(request.form.get("name_ky"), 60) or name_ru, mid))
                flash("Данные мастера сохранены.", "ok")
        elif action == "toggle":
            execute("UPDATE masters SET active = 1 - active WHERE id = ?", (mid,))
        elif action == "delete":
            if query("SELECT 1 FROM bookings WHERE master_id = ?", (mid,), one=True):
                flash("У мастера есть записи. Скройте его вместо удаления.", "error")
            else:
                execute("DELETE FROM masters WHERE id = ?", (mid,))
                flash("Мастер удалён.", "ok")
        return redirect(url_for("admin_masters"))
    return render_template("admin/masters.html", rows=query("SELECT * FROM masters ORDER BY category, id"),
                           cats=seed_data.CATEGORIES)


@app.route("/admin/inbox", methods=["GET", "POST"])
@admin_required
def admin_inbox():
    if request.method == "POST":
        kind, item_id, action = request.form.get("kind"), to_int(request.form.get("id")), request.form.get("action")
        if kind == "message" and action == "read":
            execute("UPDATE messages SET is_read = 1 WHERE id = ?", (item_id,))
        elif kind == "message" and action == "delete":
            execute("DELETE FROM messages WHERE id = ?", (item_id,))
        elif kind == "review" and action == "delete":
            execute("DELETE FROM reviews WHERE id = ?", (item_id,))
        return redirect(url_for("admin_inbox"))
    return render_template("admin/inbox.html", messages=query("SELECT * FROM messages ORDER BY is_read, id DESC"),
                           reviews=query("SELECT * FROM reviews ORDER BY id DESC LIMIT 30"))


# ---------------------------------------------------------------------------
# Страницы ошибок
# ---------------------------------------------------------------------------
def error_page(code, text_key=None):
    text = t("err.csrf") if text_key == "csrf" else t(f"error.text.{code}")
    return render_template("error.html", code=code, title=t(f"error.title.{code}"), text=text), code


@app.errorhandler(400)
def bad_request(err):
    return error_page(400, getattr(err, "description", None))


@app.errorhandler(404)
def not_found(_err):
    return error_page(404)


@app.errorhandler(413)
def too_large(_err):
    return error_page(413)


@app.errorhandler(500)
def server_error(_err):
    return error_page(500)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
