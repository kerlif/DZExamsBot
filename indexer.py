"""
indexer.py — فهرسة محتوى الموقع في SQLite + سجل مستخدمي البوت

لماذا؟ مسارات البحث والمنتدى في الموقع محمية بتحقق LiteSpeed reCAPTCHA،
والحل العملي: فهرسة دورية هادئة (طلب كل ~1.5 ث) في قاعدة محلية،
ثم يجيب البوت من القاعدة المحلية.

الجدول items:
  token فريد — url — title — level/subject/section/stream — kind — year — pdf_url
الجدول users: مستخدمو البوت (للإحصاء وإخطار المشرف بالجدد).
"""
import asyncio
import logging
import os
import sqlite3
import time
from typing import List, Optional

import config
import catalog
from scraper import parse_items, parse_sections, parse_streams

log = logging.getLogger("dzx.indexer")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE,
    kind TEXT,
    url TEXT,
    title TEXT,
    level TEXT,
    subject TEXT,
    section TEXT,
    stream TEXT,
    year TEXT,
    pdf_url TEXT,
    solution INTEGER DEFAULT 0,
    tags TEXT DEFAULT '',
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_items_level ON items(level, subject);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    first_seen INTEGER,
    last_seen INTEGER
);
"""


class Indexer:
    def __init__(self, fetcher, db_path: Optional[str] = None):
        self.fetcher = fetcher
        self.db_path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = None
        self.progress = {"running": False, "level": "", "subject": "",
                         "done": 0, "total": 0, "errors": 0, "skipped": 0,
                         "started_at": 0.0, "last_error": ""}

    # ─── قاعدة البيانات ──────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(self.db_path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            # ترحيل لقواعد قديمة: أضف الأعمدة الجديدة إن غابت
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(items)")}
            for col, ddl in (
                ("solution", "ALTER TABLE items ADD COLUMN solution INTEGER DEFAULT 0"),
                ("tags", "ALTER TABLE items ADD COLUMN tags TEXT DEFAULT ''"),
            ):
                if col not in cols:
                    self._db.execute(ddl)
            self._db.commit()
        return self._db

    def close(self):
        if self._db:
            self._db.close()
            self._db = None

    # ─── الإدراج ─────────────────────────────────────────────────────
    def upsert_items(self, items) -> int:
        db = self._conn()
        now = int(time.time())
        added = 0
        for it in items:
            if not it.token or not it.url:
                continue
            db.execute(
                """INSERT INTO items(token, kind, url, title, level, subject,
                                     section, stream, year, solution, tags,
                                     updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(token) DO UPDATE SET
                     title=excluded.title,
                     year=excluded.year,
                     solution=excluded.solution,
                     tags=excluded.tags,
                     updated_at=excluded.updated_at""",
                (it.token, it.kind, it.url, it.title, it.level, it.subject,
                 it.section, it.stream, it.year,
                 1 if getattr(it, "solution", False) else 0,
                 ",".join(getattr(it, "tags", []) or []), now),
            )
            added += 1
        db.commit()
        return added

    # ─── المستخدمون ───────────────────────────────────────────────────
    def record_user(self, user_id: int, username: str = "",
                    first_name: str = "", last_name: str = "") -> bool:
        """يسجّل مستخدمًا؛ يرجع True إن كان أول ظهور له."""
        db = self._conn()
        now = int(time.time())
        cur = db.execute(
            "INSERT OR IGNORE INTO users"
            "(user_id, username, first_name, last_name, first_seen, last_seen)"
            " VALUES(?,?,?,?,?,?)",
            (user_id, username, first_name, last_name, now, now))
        is_new = cur.rowcount == 1
        if not is_new:
            db.execute(
                "UPDATE users SET username=?, first_name=?, last_name=?,"
                " last_seen=? WHERE user_id=?",
                (username, first_name, last_name, now, user_id))
        db.commit()
        return is_new

    def count_users(self, exclude=frozenset()) -> int:
        """عدد المستخدمين المسجّلين (باستثناء المشرفين إن طُلب)."""
        db = self._conn()
        if exclude:
            marks = ",".join("?" * len(exclude))
            row = db.execute(
                f"SELECT COUNT(*) FROM users WHERE user_id NOT IN ({marks})",
                tuple(exclude)).fetchone()
        else:
            row = db.execute("SELECT COUNT(*) FROM users").fetchone()
        return row[0] if row else 0

    # ─── الفهرسة ─────────────────────────────────────────────────────
    async def targets_for_subject(self, level: str, subject: str) -> List[dict]:
        """قائمة صفحات القوائم المطلوب فهرستها لمادة معينة.

        تتعلّم الموجود فعلًا من صفحة المادة نفسها (أقسام/شعب)، فلا
        تُجرَّب توليفات غير موجودة. إن تعذّر التعلّم، تُفهرس صفحة
        المادة نفسها كصفحة عناصر مباشرة.
        """
        out = []
        if level in catalog.EXAM_LEVELS:
            return [{"level": level, "subject": subject, "section": "",
                     "stream": "", "path": f"/ar/{level}/{subject}"}]
        res = await self.fetcher.get(f"{config.BASE_URL}/ar/{level}/{subject}")
        if not res.ok:
            self.progress["errors"] += 1
            self.progress["last_error"] = f"/ar/{level}/{subject} -> {res.block.value}"
            log.warning("index skip subject page /ar/%s/%s (%s)",
                        level, subject, res.block.value)
            return out
        if level in catalog.STREAM_LEVELS:
            for s in parse_streams(level, subject, res.text):
                out.append({"level": level, "subject": subject, "section": "",
                            "stream": s, "path": f"/ar/bac/{subject}/{s}"})
            if out:
                return out
        else:
            for sec in parse_sections(level, subject, res.text):
                out.append({"level": level, "subject": subject, "section": sec,
                            "stream": "",
                            "path": f"/ar/{level}/{subject}/{sec}"})
            if out:
                return out
        # المادة بلا أقسام/شعب ظاهرة: عناصر مباشرة من صفحتها
        return [{"level": level, "subject": subject, "section": "",
                 "stream": "", "path": f"/ar/{level}/{subject}"}]

    async def index_subject(self, level: str, subject: str) -> int:
        added_total = 0
        for tg in await self.targets_for_subject(level, subject):
            url = f"{config.BASE_URL}{tg['path']}"
            res = await self.fetcher.get(url)
            if not res.ok:
                if res.block.value == "not_found":
                    # قسم غير موجود فعلًا: ضجيج معروف، يُحسب بصمت
                    self.progress.setdefault("skipped", 0)
                    self.progress["skipped"] += 1
                    log.debug("index skip %s (not_found)", tg["path"])
                else:
                    self.progress["errors"] += 1
                    self.progress["last_error"] = f"{tg['path']} -> {res.block.value}"
                    log.warning("index skip %s (%s)", tg["path"], res.block.value)
                continue
            page = parse_items(
                res.text, level=level, subject=subject,
                section=tg["section"], stream=tg["stream"],
            )
            if page.items:
                added_total += self.upsert_items(page.items)
        return added_total

    async def index_level(self, level: str, subject_filter: Optional[str] = None) -> int:
        subjects = catalog.subjects_for(level)
        if subject_filter:
            subjects = [s for s in subjects if s == subject_filter]
        total = len(subjects)
        self.progress.update(running=True, level=level, done=0, total=total,
                             errors=0, skipped=0, started_at=time.monotonic(),
                             last_error="")
        added = 0
        try:
            for i, sub in enumerate(subjects):
                self.progress["subject"] = sub
                n = await self.index_subject(level, sub)
                added += n
                self.progress["done"] = i + 1
                await asyncio.sleep(config.INDEX_SECTION_DELAY)
        finally:
            self.progress["running"] = False
        log.info("index_level(%s): +%d items", level, added)
        return added

    # ─── البحث ───────────────────────────────────────────────────────
    def item_by_token(self, token: str) -> Optional[dict]:
        db = self._conn()
        r = db.execute("SELECT * FROM items WHERE token = ?", (token,)).fetchone()
        return dict(r) if r else None

    # ─── إحصائيات ────────────────────────────────────────────────────
    def stats(self) -> dict:
        db = self._conn()
        total = db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        by_level = db.execute(
            "SELECT level, COUNT(*) c FROM items WHERE level != '' "
            "GROUP BY level ORDER BY c DESC LIMIT 20"
        ).fetchall()
        last = db.execute("SELECT MAX(updated_at) m FROM items").fetchone()["m"]
        return {
            "total": total,
            "by_level": [(r["level"], r["c"]) for r in by_level],
            "last_index": last,
        }

    def stats_text(self) -> str:
        s = self.stats()
        lines = [f"📚 إجمالي العناصر المفهرسة: {s['total']}"]
        for lv, c in s["by_level"]:
            name = catalog.LEVELS.get(lv, lv)
            lines.append(f"  • {name}: {c}")
        if s["last_index"]:
            import datetime
            dt = datetime.datetime.fromtimestamp(s["last_index"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"🕒 آخر فهرسة: {dt}")
        return "\n".join(lines)

    def progress_text(self) -> str:
        p = self.progress
        if p["running"]:
            elapsed = time.monotonic() - p["started_at"]
            return (
                f"⏳ الفهرسة جارية — المستوى: {p['level']}\n"
                f"📄 المادة الحالية: {p['subject']}\n"
                f"✅ {p['done']}/{p['total']} مادة\n"
                f"⚠️ أخطاء: {p['errors']}\n"
                f"⏱ الزمن: {int(elapsed)} ث"
            )
        if p["done"]:
            return (
                f"✔ اكتملت آخر فهرسة ({p['level']})\n"
                f"✅ {p['done']} مادة | ⚠️ {p['errors']} خطأ\n"
                f"آخر خطأ: {p['last_error'] or '—'}"
            )
        return "لا توجد فهرسة جارية. استخدم /index <level>"
