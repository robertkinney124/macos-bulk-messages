#!/usr/bin/env python3
"""
Bulk iMessage sender with post-send verification and timed SMS fallback.

Flow per row:
  1) Build message from CSV (supports {first_name}).
  2) Send via iMessage AppleScript (blue route).
     - If AppleScript errors (e.g., not iMessage-capable), we immediately retry via SMS.
  3) If AppleScript reported success, wait --verify-wait seconds and poll the Messages DB
     up to --verify-timeout. If not delivered by then, retry via SMS.

Requirements:
- macOS Messages signed in. For SMS fallback, iPhone Text Message Forwarding must be ON.
- Give Terminal Full Disk Access, OR pass --db to a readable copy of chat.db (we copy it each poll).
- AppleScripts:
    --applescript          : iMessage-first sender (e.g., send_imessage.applescript)
    --sms-applescript      : SMS-only sender (e.g., send_sms_only.applescript)

Usage example:
  python3 bulk_imessage.py contacts.csv \
    --message "Hi {first_name}, ..." \
    --applescript send_imessage.applescript \
    --sms-applescript send_sms_only.applescript \
    --verify-imessage \
    --verify-wait 2 \
    --verify-timeout 10 \
    --track-link \
    --log-file send_log.csv \
    --limit 5
"""

import csv
import argparse
import subprocess
import time
from pathlib import Path
import re
import sys
import uuid
import datetime
import sqlite3
import shutil
import tempfile
import os
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

# ---------------- helpers ----------------

def normalize_phone(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if re.fullmatch(r"\+\d{8,20}", s.replace(" ", "")):
        return re.sub(r"\s+", "", s)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if 8 <= len(digits) <= 15 and not digits.startswith("0"):
        return f"+{digits}"
    return None

def digits_only(s): return re.sub(r"\D", "", s or "")

def find_first_url(text):
    m = re.search(r"(https?://\S+)", text or "")
    if not m:
        return None, None
    return m.group(1), m.span(1)

def add_query_param(url, key, value):
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q[key] = value
    new_query = urlencode(q, doseq=True)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))

def personalize_link_in_message(msg, phone_e164, field_name):
    url, span = find_first_url(msg)
    if not url:
        return msg
    cid_val = re.sub(r"\D", "", phone_e164 or "")
    new_url = add_query_param(url, field_name, cid_val)
    start, end = span
    return msg[:start] + new_url + msg[end:]

def run_osascript(applescript_path: Path, phone: str, message: str, dry_run: bool = False):
    if dry_run:
        print(f"[DRY RUN] Would send to {phone}: {message}")
        return True, "dry-run"
    try:
        res = subprocess.run(["osascript", str(applescript_path), phone, message],
                             capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return True, (res.stdout or "").strip() or "sent"
        return False, f"osascript error: {res.stderr.strip() or res.stdout.strip() or 'osascript failed'}"
    except Exception as e:
        return False, str(e)

# ---------------- DB verification ----------------

def has_col(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def copy_db(src_db: Path) -> Path:
    td = tempfile.TemporaryDirectory()
    tmp_db = Path(td.name) / "chat_copy.db"
    shutil.copy2(src_db, tmp_db)
    # Return both path and the tempdir handle to keep it alive
    return tmp_db, td

def find_handle_for_phone(conn: sqlite3.Connection, phone_e164: str):
    want = digits_only(phone_e164)
    c = conn.cursor()
    c.execute("SELECT ROWID, id FROM handle ORDER BY ROWID DESC")
    for rowid, addr in c.fetchall():
        if digits_only(addr).endswith(want):
            return rowid, addr
    return None, None

def latest_outgoing_for_handle(conn: sqlite3.Connection, handle_rowid: int):
    c = conn.cursor()
    cols = [r[1] for r in c.execute("PRAGMA table_info(message)")]
    sel = """SELECT m.ROWID, m.date, m.text {isd} {dd}
             FROM message m
             WHERE m.is_from_me=1 AND m.handle_id=?
             ORDER BY m.date DESC
             LIMIT 1""".format(
        isd=", m.is_delivered" if "is_delivered" in cols else ", NULL",
        dd=", m.date_delivered" if "date_delivered" in cols else ", NULL"
    )
    row = c.execute(sel, (handle_rowid,)).fetchone()
    return row, ("is_delivered" in cols), ("date_delivered" in cols)

def is_undelivered(row, has_is_delivered, has_date_delivered):
    # row = (rowid, date, text, is_delivered?, date_delivered?)
    if row is None:
        return True
    is_del = row[3] if has_is_delivered else None
    date_del = row[4] if has_date_delivered else None
    undel_by_is = (has_is_delivered and is_del == 0)
    undel_by_date = (has_date_delivered and (date_del is None or date_del == 0))
    # If we have no delivery columns at all, treat as undetermined -> keep polling
    if not has_is_delivered and not has_date_delivered:
        return True
    return bool(undel_by_is or undel_by_date)

def verify_delivery(src_db: Path, phone: str, wait: float, timeout: float) -> bool:
    """
    Returns True if delivered within timeout, False if still undelivered after timeout.
    """
    # Poll loop
    start = time.time()
    time.sleep(max(0.0, wait))
    while True:
        tmp_db, td = copy_db(src_db)
        try:
            conn = sqlite3.connect(str(tmp_db))
            hid, _ = find_handle_for_phone(conn, phone)
            if hid:
                row, h1, h2 = latest_outgoing_for_handle(conn, hid)
                undel = is_undelivered(row, h1, h2)
                conn.close()
            else:
                undel = True
        finally:
            td.cleanup()

        if not undel:
            return True  # delivered
        if time.time() - start >= timeout:
            return False  # still undelivered
        time.sleep(1.0)

# ---------------- main ----------------

def main():
    p = argparse.ArgumentParser(description="Bulk iMessage with verify+SMS fallback.")
    p.add_argument("csv_path", help="CSV with at least 'phone'. Optional 'first_name'")
    p.add_argument("--message", required=True, help="Message template (supports {first_name})")
    p.add_argument("--applescript", default="send_imessage.applescript", help="AppleScript for iMessage-first")
    p.add_argument("--sms-applescript", default="send_sms_only.applescript", help="AppleScript for SMS fallback")
    p.add_argument("--delay", type=float, default=2.5, help="Delay between rows (seconds)")
    p.add_argument("--dry-run", action="store_true", help="No sends; just print/log")
    p.add_argument("--limit", type=int, default=0, help="Only first N rows")
    p.add_argument("--log-file", default="send_log.csv", help="Output log CSV")
    p.add_argument("--track-link", action="store_true", help="Append ?cid=<digits> to first link")
    p.add_argument("--link-field-name", default="cid", help="Query param name for link tracking")

    # verification knobs
    p.add_argument("--verify-imessage", action="store_true",
                   help="After iMessage send, poll Messages DB; if still undelivered after timeout, send SMS")
    p.add_argument("--verify-wait", type=float, default=2.0, help="Seconds to wait before first check")
    p.add_argument("--verify-timeout", type=float, default=8.0, help="Max seconds to wait before falling back to SMS")
    p.add_argument("--db", help="Path to chat.db (default: ~/Library/Messages/chat.db)")

    args = p.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: CSV not found at {csv_path}", file=sys.stderr)
        sys.exit(1)

    im_script = Path(args.applescript)
    sms_script = Path(args.sms_applescript)
    if not args.dry_run and not im_script.exists():
        print(f"ERROR: iMessage AppleScript not found at {im_script}", file=sys.stderr); sys.exit(1)
    if not args.dry_run and not sms_script.exists():
        print(f"ERROR: SMS AppleScript not found at {sms_script}", file=sys.stderr); sys.exit(1)

    # determine DB path
    default_db = Path.home() / "Library" / "Messages" / "chat.db"
    src_db = Path(os.path.expanduser(args.db)) if args.db else default_db
    if args.verify_imessage and not src_db.exists():
        print(f"ERROR: Messages DB not found at {src_db}. Grant Full Disk Access or pass --db to a copy.", file=sys.stderr)
        sys.exit(1)

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    log_path = Path(args.log_file)

    def log_row(phone, first_name, status, info="", message_text=""):
        with open(log_path, "a", newline="", encoding="utf-8") as lf:
            w = csv.writer(lf)
            if lf.tell() == 0:
                w.writerow(["timestamp","phone","first_name","status","info","run_id","message"])
            w.writerow([datetime.datetime.now().isoformat(timespec="seconds"),
                        phone, first_name, status, info, run_id, message_text])

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("ERROR: CSV appears empty or missing headers.", file=sys.stderr); sys.exit(1)

        headers_lower = [h.lower() for h in reader.fieldnames]
        def h(name):
            name = name.lower()
            for i, hdr in enumerate(headers_lower):
                if hdr == name:
                    return reader.fieldnames[i]
            return None

        phone_h = h("phone")
        first_h = h("first_name")
        if not phone_h:
            print(f"ERROR: CSV must include 'phone'. Found: {reader.fieldnames}", file=sys.stderr)
            sys.exit(1)

        rows = list(reader)
        total = len(rows)
        print(f"Loaded {total} rows from {csv_path}")

        processed = sent = failed = sms_sent = sms_failed = 0

        for row in rows:
            if args.limit and processed >= args.limit:
                break
            processed += 1

            raw_phone = row.get(phone_h, "")
            phone = normalize_phone(raw_phone)
            if not phone:
                failed += 1
                info = f"Unusable phone: {raw_phone!r}"
                print(f"[SKIP] {info}")
                log_row("", row.get(first_h, "") if first_h else "", "failed", info, "")
                continue

            first_name = (row.get(first_h, "") or "").strip() if first_h else ""
            msg = args.message.format(first_name=first_name)
            if args.track_link:
                msg = personalize_link_in_message(msg, phone, args.link_field_name)

            # 1) Send iMessage
            ok, info = run_osascript(im_script, phone, msg, dry_run=args.dry_run)
            if not ok:
                print(f"[BLUE FAIL] {phone}: {info}")
                log_row(phone, first_name, "failed", f"imessage:{info}", msg)
                # Immediate SMS fallback
                ok2, info2 = run_osascript(sms_script, phone, msg, dry_run=args.dry_run)
                if ok2:
                    sms_sent += 1
                    print(f"[SMS AFTER BLUE ERROR] {phone} ({info2})")
                    log_row(phone, first_name, "sms_sent", info2, msg)
                else:
                    sms_failed += 1
                    print(f"[SMS FAIL AFTER BLUE ERROR] {phone}: {info2}")
                    log_row(phone, first_name, "sms_failed", info2, msg)
                time.sleep(args.delay)
                continue

            # iMessage AppleScript returned success
            sent += 1
            print(f"[BLUE SENT] {phone} ({info})")
            log_row(phone, first_name, "sent", info, msg)

            # 2) Verify and timed fallback
            if args.verify_imessage and not args.dry_run:
                delivered = verify_delivery(src_db, phone, args.verify_wait, args.verify_timeout)
                if delivered:
                    print(f"[DELIVERED OK] {phone}")
                else:
                    print(f"[UNDELIVERED → SMS] {phone}")
                    ok3, info3 = run_osascript(sms_script, phone, msg, dry_run=False)
                    if ok3:
                        sms_sent += 1
                        print(f"[SMS RETRY OK] {phone} ({info3})")
                        log_row(phone, first_name, "sms_sent", info3, msg)
                    else:
                        sms_failed += 1
                        print(f"[SMS RETRY FAIL] {phone}: {info3}")
                        log_row(phone, first_name, "sms_failed", info3, msg)

            time.sleep(args.delay)

        print(f"Done. Processed: {processed}/{total}. Blue-sent: {sent}, Blue-failed: {failed}, "
              f"SMS-sent: {sms_sent}, SMS-failed: {sms_failed}")
        print(f"Log at: {log_path}")

if __name__ == "__main__":
    main()
