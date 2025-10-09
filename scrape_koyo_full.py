# -*- coding: utf-8 -*-
"""
KOYO scraper – Playwright (Sync) – نسخة قوية محدثة ومصححة

تشغيل أمثلة:
    python scrape_koyo_full.py --headless 0 --months 2 --service-index 1 --staff-index 0
"""

import re
import csv
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://www.koyo-wellness.com/book/"
OUT_DIR = Path("data/koyo")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = OUT_DIR / "koyo_appointments.csv"
JSON_PATH = OUT_DIR / "koyo_appointments.json"

CHOSEN_SERVICE = ""
CHOSEN_STAFF = ""

# -------------------- أدوات مساعدة --------------------

def utc_ts():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def log(msg):
    print(msg, flush=True)

def safe_text(el):
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""

def L(scope_or_page, css: str):
    # استخدام :light() لاختراق الشادو في Healcode (ويعمل عادي لو مافيش شادو)
    return scope_or_page.locator(f":light({css})")

def accept_cookies(page):
    for sel in [
        '[data-cky-tag="accept-button"]',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
    ]:
        try:
            loc = page.locator(sel)
            if loc.count():
                loc.first.click(timeout=1500)
                log("clicked cookie accept")
                break
        except Exception:
            pass

# -------------------- انتظار تهيئة الودجت --------------------

def wait_healcode_ready(page, max_wait_ms=30000):
    """
    ينتظر ظهور healcode-widget (appointments) ويضمن أن UI أصبح قابل للتعامل.
    يرجّع locator للودجت (الهوست).
    """
    hw = page.locator(":light(healcode-widget[data-type='appointments'])").first
    hw.wait_for(state="attached", timeout=max_wait_ms)

    try:
        hw.scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(600)

    def ui_visible():
        sel = ":light(.hc-calendar, .hc-availability, .hc-times, [data-date], .appointments, .hc-appointments, li.hc-appointment, li.appointment, form)"
        try:
            return page.locator(sel).count() > 0
        except Exception:
            return False

    deadline = time.time() + (max_wait_ms/1000.0)
    while time.time() < deadline:
        if ui_visible():
            return hw

        # جرّب أزرار عامة داخل الشادو تفتح النتائج/التقويم
        for css in [
            ":light(button[type='submit'])", ":light(input[type='submit'])",
            ":light(button:has-text('Search'))", ":light(a:has-text('Search'))",
            ":light(button:has-text('Find'))",   ":light(a:has-text('Find'))",
            ":light(button:has-text('Book'))",   ":light(a:has-text('Book'))",
            ":light(button:has-text('Show times'))", ":light(a:has-text('Show times'))",
            ":light(.hc-view-schedule)", ":light(.hc-show-times)", ":light(.hc-availability__toggle)",
        ]:
            try:
                loc = page.locator(css)
                if loc.count():
                    try: loc.first.scroll_into_view_if_needed(timeout=1200)
                    except Exception: pass
                    try:
                        loc.first.click(timeout=1500)
                        page.wait_for_timeout(800)
                        if ui_visible():
                            return hw
                    except Exception:
                        pass
            except Exception:
                pass

        # Scroll لتحفيز التحميل الكسول
        try:
            page.mouse.wheel(0, 800); page.wait_for_timeout(400)
            page.mouse.wheel(0, -600); page.wait_for_timeout(300)
        except Exception:
            pass

    return hw  # حتى لو لم نر UI، نرجّعه لتكملة التشخيص

# -------------------- اختيار خدمة/موظف --------------------

def smart_select(scope, kind="Location", index=0):
    like = [
        f'select[name*="{kind}" i]', f'select[id*="{kind}" i]',
        f'select[name*="{kind[:4]}" i]', f'select[id*="{kind[:4]}" i]',
    ]
    sel = None
    for css in like:
        loc = L(scope, css)
        if loc.count():
            sel = loc.first
            break
    if not sel:
        log(f"no {kind} select (ok)")
        return None

    opts = sel.locator("option")
    n = opts.count()
    valid = []
    for i in range(n):
        txt = (opts.nth(i).inner_text() or "").strip()
        val = (opts.nth(i).get_attribute("value") or "").strip()
        if not val or not txt:
            continue
        low = txt.lower()
        if any(k in low for k in ["select", "all ", "any "]):
            continue
        valid.append((i, val, txt))
    if not valid:
        log(f"{kind} select exists but only generic options")
        return None

    choice = valid[min(index, len(valid)-1)]
    sel.select_option(index=choice[0])
    try:
        sel.evaluate("el => {el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));}")
    except Exception:
        pass
    log(f"selected {kind}: {choice[0]} -> {choice[2]}")
    time.sleep(0.4)
    return choice[2]

def choose_service(scope, service_index):
    candidates = [
        'select[id*="Service" i]', 'select[name*="Service" i]',
        'select[id*="Session" i]', 'select[name*="Session" i]',
        'select[id*="Treatment" i]', 'select[name*="Treatment" i]',
        'select'
    ]
    target = None
    for css in candidates:
        loc = L(scope, css)
        if loc.count():
            target = loc.first
            break
    if not target:
        log("no <select> found for services (maybe pre-filtered).")
        return None

    opts = target.locator("option")
    options = []
    for i in range(opts.count()):
        txt = safe_text(opts.nth(i))
        val = (opts.nth(i).get_attribute("value") or "").strip()
        if not txt or not val:
            continue
        if re.search(r'\bselect\b|choose|all services', txt, re.I):
            continue
        options.append((i, val, txt))
    if not options:
        log("service select has no real options")
        return None

    idx0 = max(0, min(len(options)-1, service_index-1))
    i_opt, val, txt = options[idx0]
    target.select_option(index=i_opt)
    try:
        target.evaluate("el => el.dispatchEvent(new Event('change',{bubbles:true}))")
    except Exception:
        pass
    time.sleep(0.5)
    log(f"selected service: {txt}")
    return txt

def real_staff_select(scope, index=0):
    staff_like = [
        'select[name*="Instructor" i]','select[id*="Instructor" i]',
        'select[name*="Staff" i]','select[id*="Staff" i]',
        'select[name*="Therapist" i]','select[id*="Therapist" i]'
    ]
    sel = None
    for css in staff_like:
        loc = L(scope, css)
        if loc.count():
            sel = loc.first
            break
    if not sel:
        log("no staff select (fine)")
        return None

    opts = sel.locator("option")
    n = opts.count()
    valid = []
    for i in range(n):
        txt = (opts.nth(i).inner_text() or "").strip()
        val = (opts.nth(i).get_attribute("value") or "").strip()
        if not val or not txt:
            continue
        low = txt.lower()
        if any(k in low for k in ["select","all instructors","all staff"]):
            continue
        valid.append((i, val, txt))
    if not valid:
        log("staff select exists but only generic options")
        return None

    chosen = valid[min(index, len(valid)-1)]
    sel.select_option(index=chosen[0])
    try:
        sel.evaluate("el => el.dispatchEvent(new Event('change',{bubbles:true}))")
    except Exception:
        pass
    log(f"selected staff: {chosen[0]} -> {chosen[2]}")
    time.sleep(0.3)
    return chosen[2]

# -------------------- فتح التقويم/النتائج --------------------

def open_calendar_ui(scope):
    triggers = [
        'input[type="submit"]', 'button[type="submit"]',
        'button:has-text("Search")', 'a:has-text("Search")',
        'button:has-text("Find")',   'a:has-text("Find")',
        'button:has-text("Find an appointment")', 'a:has-text("Find an appointment")',
        'button:has-text("Book")', 'a:has-text("Book")',
        'button:has-text("Show times")','a:has-text("Show times")',
        '.hc-view-schedule','.hc-show-times','.hc-availability__toggle'
    ]
    for sel in triggers:
        try:
            loc = L(scope, sel)
            if loc.count():
                try: loc.first.scroll_into_view_if_needed(timeout=1200)
                except Exception: pass
                try:
                    loc.first.click(timeout=1800)
                    time.sleep(0.7)
                    return True
                except Exception:
                    pass
        except Exception:
            pass

    # محاولة عامة بالـ JS داخل الشادو
    try:
        clicked = scope.evaluate("""(el)=>{
          const root = el.shadowRoot || el;
          const pick = (s)=> root.querySelector(s);
          const sels = ['button[type="submit"]','input[type="submit"]','button','a','.hc-view-schedule','.hc-show-times','.hc-availability__toggle'];
          for (const s of sels){
            const b = pick(s);
            if (!b) continue;
            const t = (b.textContent||'').toLowerCase();
            if (b.type==='submit' || /search|find|book|time|avail/.test(t)){ b.click(); return true; }
          }
          return false;
        }""")
        if clicked:
            time.sleep(0.7)
            return True
    except Exception:
        pass
    return False

def try_search_or_submit(scope):
    open_calendar_ui(scope)

def poll_for_results(scope, wait_secs=14, check_interval=0.7):
    patterns = [
        '.appointments','ul.appointments','li.appointment',
        '.hc_schedule','ul.hc-appointments','li.hc-appointment',
        'a:has-text("Book")','button:has-text("Book")'
    ]
    steps = int(wait_secs / max(0.1, check_interval))
    for _ in range(steps):
        for p in patterns:
            try:
                if L(scope, p).count() > 0:
                    return True
            except Exception:
                pass
        time.sleep(check_interval)
    return False

def expand_times(scope, max_cards=20):
    triggers = [
        'button:has-text("Show times")',
        'button:has-text("View times")',
        'button:has-text("View schedule")',
        'button:has-text("View availability")',
        'a:has-text("Show times")',
        'a:has-text("View schedule")',
        'a:has-text("Book now")',
        '.hc-view-schedule', '.hc-availability__toggle', '.hc-show-times',
    ]
    clicked = 0
    for sel in triggers:
        try:
            loc = L(scope, sel)
            cnt = min(loc.count(), max_cards)
            for i in range(cnt):
                try:
                    b = loc.nth(i)
                    b.scroll_into_view_if_needed(timeout=1000)
                    b.click(timeout=1500)
                    time.sleep(0.6)
                    clicked += 1
                except Exception:
                    pass
            if clicked:
                break
        except Exception:
            pass
    if clicked:
        log(f"expanded {clicked} result card(s)")
    else:
        log("no result-cards to expand (ok)")

def day_cells_locator(scope):
    loc = L(scope, '[data-date]:not([disabled])')
    if loc.count():
        return loc
    return L(scope, "button:not([disabled])").filter(
        has_text=re.compile(r"^\s*\d{1,2}\s*$")
    )

def click_next_month(scope):
    candidates = [
        'button[aria-label*="Next" i]',
        'a[aria-label*="Next" i]',
        '.ui-datepicker-next', '.ui-datepicker-next a',
        '.hc-next', '.hc-calendar__nav--next',
        '[data-action="next"]',
        'button[title*="Next" i]',
    ]
    for sel in candidates:
        try:
            loc = L(scope, sel)
            if loc.count():
                try:
                    loc.first.click(timeout=1200)
                    time.sleep(0.6)
                    return True
                except Exception:
                    pass
        except Exception:
            pass

    try:
        btn = L(scope, "button").filter(has_text=re.compile(r"^(?:Next|›|>>)$", re.I))
        if btn.count():
            btn.first.click(timeout=1200)
            time.sleep(0.6)
            return True
    except Exception:
        pass

    # JS fallback
    try:
        clicked = scope.evaluate(
            """(el) => {
                const root = el.shadowRoot || el;
                const sels = [
                  'button[aria-label*="Next" i]','a[aria-label*="Next" i]',
                  '.ui-datepicker-next', '.ui-datepicker-next a',
                  '.hc-next', '.hc-calendar__nav--next',
                  '[data-action="next"]','button[title*="Next" i]'
                ];
                for (const s of sels) { const n = root.querySelector(s); if (n) { n.click(); return true; } }
                const nodes = root.querySelectorAll('button, a');
                for (const n of nodes) {
                  const t = (n.textContent || '').trim().toLowerCase();
                  if (t === 'next' || t.includes('›') || t.includes('>>')) { n.click(); return true; }
                }
                return false;
            }"""
        )
        if clicked:
            time.sleep(0.6)
            return True
    except Exception:
        pass

    return False

# -------------------- التجميع --------------------

def collect(scope, current_date_txt=None):
    """
    تجمع كل الفتحات من نتائج الودجت مع تفاصيل إضافية.
    - current_date_txt: مرَّر تاريخ اليوم المختار (مثلاً من data-date) إن أمكن، وإلا نحاول استنتاجه.
    ترجع list[dict].
    """
    rows = []
    seen = set()

    # أنماط بطاقات/عناصر الفتحات الشائعة في Healcode/Mindbody
    card_pats = [
        'li.hc-appointment', 'li.appointment', '.hc-appointment',
        '.hc-schedule-item', '.appointments li', '.hc-appointments li'
    ]

    def text(el, sel=None):
        try:
            tgt = el.locator(sel) if sel else el
            return (tgt.first.inner_text() or "").strip()
        except Exception:
            return ""

    def attr(el, name):
        try:
            return (el.get_attribute(name) or "").strip()
        except Exception:
            return ""

    # مُعينات ريجيكس
    rx_time   = re.compile(r'\b(\d{1,2}:\d{2}\s?(?:AM|PM)?)\b', re.I)
    rx_price  = re.compile(r'([£$€]\s?\d+(?:[.,]\d{2})?)')
    rx_dur    = re.compile(r'(\d{2,3})\s*(?:min|mins|minutes)\b', re.I)
    rx_spots  = re.compile(r'(?:spots?\s*left|remaining)\s*[:\-]?\s*(\d+)', re.I)
    rx_staff  = re.compile(r'(?:with|by)\s+([A-Z][A-Za-z.\-\' ]{1,40})')
    rx_room   = re.compile(r'(?:room|studio|resource)\s*[:\-]\s*([^\n,|]+)', re.I)
    rx_tz     = re.compile(r'\b(?:UTC|GMT|BST|CET|CEST|EST|EDT|PST|PDT)\b', re.I)

    # ابحث عن كل كارت موعد
    found_any = False
    for pat in card_pats:
        cards = scope.locator(pat)
        cnt = cards.count()
        for i in range(cnt):
            found_any = True
            card = cards.nth(i)
            raw = text(card)
            # زر الحجز + الرابط
            book_btn = card.locator('a:has-text("Book"), button:has-text("Book")')
            book_url = ""
            if book_btn.count():
                try:
                    href = attr(book_btn.first, "href")
                    data_url = attr(book_btn.first, "data-url")
                    book_url = (href or data_url or "").strip()
                except Exception:
                    pass

            # أوقات
            times = rx_time.findall(raw)
            start_local = times[0].upper() if times else ""
            end_local   = times[1].upper() if len(times) > 1 else ""

            # خدمة/عنوان/ستاف/سعر/مدة/غرفة/منطقة زمنية
            title   = text(card, '.hc-appointment__title, .appointment__title, .hc-title, h3, .title')
            service = title or ""
            staff   = text(card, '.hc-appointment__staff, .staff, .instructor')
            if not staff:
                m = rx_staff.search(raw)
                if m: staff = m.group(1).strip()

            price = ""
            m = rx_price.search(raw)
            if m: price = m.group(1).strip()

            dur_min = ""
            # جرّب من النص أولًا
            m = rx_dur.search(raw if service == "" else f"{service}\n{raw}")
            if m: dur_min = int(m.group(1))
            # ثم من data-attributes
            if not dur_min:
                for name in ["data-duration","data-length","data-minutes"]:
                    v = attr(card, name)
                    if v.isdigit():
                        dur_min = int(v); break

            room = ""
            m = rx_room.search(raw)
            if m: room = m.group(1).strip()

            tz = ""
            m = rx_tz.search(raw)
            if m: tz = m.group(0).upper()

            # سعة/المتبقي
            spots_left = ""
            m = rx_spots.search(raw)
            if m: spots_left = int(m.group(1))

            # date_local (من الخاصية إن مررناها لنا أو من أسلاف الكارت)
            date_local = current_date_txt or attr(card, "data-date")
            if not date_local:
                try:
                    # فتش أب لعنصر معه data-date
                    date_el = card.locator("xpath=ancestor::*[@data-date][1]")
                    if date_el.count():
                        date_local = attr(date_el.first, "data-date")
                except Exception:
                    pass

            # مفاتيح تعريفية إضافية
            slot_id = attr(card, "data-id") or attr(card, "id")
            staff_attr = attr(card, "data-staff") or attr(card, "data-instructor")
            if (not staff) and staff_attr:
                staff = staff_attr

            # مفتاح تكرار لتجنب التكرار
            dedup_key = (date_local or "", service, staff, start_local, book_url or raw[:80])
            if dedup_key in seen: 
                continue
            seen.add(dedup_key)

            row = {
                "source_site": "koyo-wellness.com",
                "venue_name": "KOYO Wellness",
                "venue_loc": "London",
                "date_local": date_local or "",
                "start_time_local": start_local,
                "end_time_local": end_local,
                "duration_min": dur_min,
                "service": service,
                "staff": staff,
                "room": room,
                "price": price,
                "spots_left": spots_left,
                "book_url": book_url,
                "slot_id": slot_id,
                "timezone_hint": tz,
                "notes": raw[:500].replace("\r"," ").replace("\n"," ").strip(),
                "scrape_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            rows.append(row)

    # كـ fallback: لو ما لقيناش كروت واضحة لكن ظهر زر Book في عناصر عامة
    if not found_any:
        books = scope.locator('a:has-text("Book"), button:has-text("Book")')
        for i in range(min(200, books.count())):
            b = books.nth(i)
            parent = b.locator("xpath=ancestor-or-self::*[1]")
            raw = text(parent)
            times = rx_time.findall(raw)
            start_local = times[0].upper() if times else ""
            price = ""
            m = rx_price.search(raw)
            if m: price = m.group(1).strip()
            book_url = attr(b, "href") or attr(b, "data-url") or ""
            row = {
                "source_site": "koyo-wellness.com",
                "venue_name": "KOYO Wellness",
                "venue_loc": "London",
                "date_local": current_date_txt or "",
                "start_time_local": start_local,
                "end_time_local": "",
                "duration_min": "",
                "service": "",
                "staff": "",
                "room": "",
                "price": price,
                "spots_left": "",
                "book_url": book_url,
                "slot_id": "",
                "timezone_hint": "",
                "notes": raw[:500].replace("\r"," ").replace("\n"," ").strip(),
                "scrape_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            rows.append(row)

    log(f"collect(): found detailed slots ~ {len(rows)}")
    return rows






def collect_month(scope, max_days=31, next_month=True):
    rows_total = []

    # تأمين ظهور خلايا الأيام بمحاولات
    for attempt in range(3):
        cells = day_cells_locator(scope)
        if cells.count() > 0:
            break
        log("no selectable dates found in this month. trying to open calendar...")
        open_calendar_ui(scope)
        try:
            scope.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        scope.page.wait_for_timeout(700)

    cells = day_cells_locator(scope)
    cnt = min(max_days, cells.count())
    if cnt == 0:
        log("still no selectable dates in this month.")

    for i in range(cnt):
        btn = cells.nth(i)
        try:
            label = btn.get_attribute("data-date") or (btn.inner_text() or "").strip()
        except Exception:
            label = str(i+1)

        try:
            btn.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass

        try:
            btn.click(timeout=1500)
            log(f"picked day -> {label}")
        except Exception:
            continue

        try_search_or_submit(scope)

        _ = poll_for_results(scope, wait_secs=14, check_interval=0.7)
        expand_times(scope)

        found = collect(scope, current_date_txt=label)

        # املأ الخدمة/الستاف المختارين (إن وُجدت)
        for r in found:
            if CHOSEN_SERVICE and not r.get("service"):
                r["service"] = CHOSEN_SERVICE
            if CHOSEN_STAFF and not r.get("staff"):
                r["staff"] = CHOSEN_STAFF

        if found:
            log(f"collected {len(found)} rows for day {label}")
            rows_total.extend(found)
        else:
            log(f"no slots for day {label}")

        time.sleep(0.3)

    if not rows_total and next_month:
        if click_next_month(scope):
            log("moved to next month")
            rows_total.extend(collect_month(scope, max_days=max_days, next_month=False))
        else:
            log("failed to click next-month")

    return rows_total

# -------------------- إخراج وتشخيص --------------------

def save_diagnostics(page, rows):
    ts = utc_ts()
    html_p = OUT_DIR / f"widget_{ts}.html"
    png_p  = OUT_DIR / f"widget_{ts}.png"
    try:
        html_p.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(png_p), full_page=True)
        log(f"HTML_SAVED: {html_p}\nPNG_SAVED:  {png_p}")
    except Exception as e:
        log(f"diag save err: {e}")

    try:
        existing = []
        if JSON_PATH.exists():
            existing = json.loads(JSON_PATH.read_text(encoding="utf-8") or "[]")
        existing.extend(rows)
        JSON_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def append_csv(rows):
    """
    يكتب CSV موحّد الحقول مع إزالة التكرار، مع إعادة محاولة لو الملف مقفول.
    """
    if not rows:
        log(f"WROTE_ROWS:0 -> {CSV_PATH}")
        return

    fieldnames = [
        "source_site","venue_name","venue_location",
        "date_local","start_time_local","end_time_local",
        "duration_min","service","staff","room","price",
        "capacity","spots_left","booked_count",
        "url","slot_id","timezone_hint","notes","scrape_timestamp_utc"
    ]

    # إزالة تكرار حسب (التاريخ + الوقت + الخدمة + الستاف)
    dedup_keys = ("date_local","start_time_local","service","staff")
    seen = set(); uniq = []
    for r in rows:
        key = tuple((r.get(k) or "").strip() for k in dedup_keys)
        if key in seen: 
            continue
        seen.add(key); uniq.append(r)

    # حمّل القديم (إن وُجد) وادمجه
    existing = []
    if CSV_PATH.exists():
        try:
            with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                existing = list(csv.DictReader(f))
        except Exception:
            existing = []

    all_rows = existing + uniq
    seen = set(); merged = []
    for r in all_rows:
        key = tuple((r.get(k) or "").strip() for k in dedup_keys)
        if key in seen: 
            continue
        seen.add(key); merged.append(r)

    # إعادة محاولة الكتابة
    backoff = 0.6
    for attempt in range(10):
        try:
            with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                for r in merged:
                    w.writerow({k: r.get(k, "") for k in fieldnames})
            log(f"WROTE_ROWS:{len(merged)} -> {CSV_PATH}")
            return
        except PermissionError:
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 3.0)
        except FileNotFoundError:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log(f"CSV write error: {e}")
            break

    # في حال الفشل النهائي: اكتب نسخة احتياطية
    fallback = OUT_DIR / f"koyo_appointments_{utc_ts()}.csv"
    try:
        with fallback.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in merged:
                w.writerow({k: r.get(k, "") for k in fieldnames})
        log(f"[FALLBACK] WROTE_ROWS:{len(merged)} -> {fallback}")
    except Exception as e:
        log(f"[FALLBACK FAILED] {e}")

# -------------------- التنفيذ --------------------

def scrape(months=2, service_index=1, staff_index=0, headless=True):
    global CHOSEN_SERVICE, CHOSEN_STAFF

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-features=SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
            locale="en-GB",
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()
        page.set_default_timeout(20000)

        log(f"GO TO: {URL}")
        page.goto(URL, wait_until="domcontentloaded")
        accept_cookies(page)
        page.wait_for_timeout(400)

        scope = wait_healcode_ready(page)
        log("healcode widget ready")

        try:
            scope.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(300)

        # اختيارات اختيارية (إن وُجدت)
        CHOSEN_SERVICE = choose_service(scope, service_index) or ""
        if not CHOSEN_SERVICE:
            log("no <select> found for services (maybe pre-filtered).")

        CHOSEN_STAFF = ""
        if staff_index and staff_index > 0:
            s = real_staff_select(scope, index=staff_index-1)
            CHOSEN_STAFF = s or ""

        # افتح التقويم/النتائج
        open_calendar_ui(scope)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

        # لف الأيام/الأشهر
        all_rows = collect_month(scope, max_days=31, next_month=(months > 1))

        save_diagnostics(page, all_rows)
        append_csv(all_rows)

        context.close()
        browser.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-index", type=int, default=1, help="1-based index of service option")
    ap.add_argument("--staff-index", type=int, default=0, help="1-based index of staff option (0=skip)")
    ap.add_argument("--months", type=int, default=2, help="how many months to scan (>=1)")
    ap.add_argument("--headless", type=int, default=1, help="1=headless, 0=see browser")
    args = ap.parse_args()
    scrape(
        months=max(1, args.months),
        service_index=max(1, args.service_index),
        staff_index=max(0, args.staff_index),
        headless=bool(args.headless),
    )

if __name__ == "__main__":
    main()
