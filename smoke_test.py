"""
smoke_test.py — اختبار حي شامل لمكونات البوت (بدون تيليغرام)
يختبر: الجلب عبر التجاوز، التصفح الهرمي، العناصر، PDF، الشهادات، الشعب،
الفهرسة والبحث.
"""
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.ERROR)

import config
import catalog
from fetcher import Fetcher
from warp import WarpManager
from service import ContentService
from indexer import Indexer

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("✅ " if cond else "❌ ") + name + (f" — {extra}" if extra else ""))


async def main():
    fetcher = Fetcher(WarpManager())
    svc = ContentService(fetcher)

    # 1) صفحة مستوى + مواد ثابتة
    print("\n== 1) catalog ==")
    subs = catalog.subjects_for("1as")
    check("catalog 1as subjects", len(subs) >= 10, f"{len(subs)} subjects")

    # 2) صفحة مادة مدرسية → أقسام
    print("\n== 2) subject page (school) ==")
    info = await svc.subject_page("1as", "mathematiques")
    check("school sections", info["type"] == "school" and len(info["sections"]) >= 5,
          str(info["sections"]))

    # 3) عناصر قسم d1
    print("\n== 3) section items ==")
    page = await svc.section_items("1as", "mathematiques", "d1")
    check("d1 items parsed", len(page.items) > 50, f"{len(page.items)} items")
    first = page.items[0]
    check("item has token+url", bool(first.token and first.url),
          f"{first.kind} {first.url[:40]}")
    check("item title arabic", bool(first.title and "فروض" in first.title),
          first.title[:50])

    # 4) تفاصيل عنصر + تنزيل PDF
    print("\n== 4) item detail + PDF ==")
    detail = await svc.item_detail(first)
    check("pdf url found", bool(detail.get("pdf_url")),
          (detail.get("pdf_url") or "")[:80])
    if detail.get("pdf_url"):
        path = await svc.download_item_pdf(first)
        okp = bool(path and os.path.exists(path) and os.path.getsize(path) > 50000)
        check("pdf downloaded", okp,
              f"{os.path.getsize(path) if path else 0} bytes")
        if path:
            os.remove(path)

    # 5) شهادة BEM → عناصر annales
    print("\n== 5) exam level (BEM) ==")
    info = await svc.subject_page("bem", "mathematiques")
    n_bem = len(info["items"].items) if info["items"] else 0
    check("bem items", info["type"] == "exam" and n_bem > 10, f"{n_bem} items")
    if n_bem:
        it = info["items"].items[0]
        check("bem item kind=annales", it.kind == "annales", it.url[:40])
        det = await svc.item_detail(it)
        check("bem item pdf", bool(det.get("pdf_url")),
              (det.get("pdf_url") or "")[:70])

    # 6) بكالوريا → شعب
    print("\n== 6) BAC streams ==")
    info = await svc.subject_page("bac", "mathematiques")
    check("bac streams", info["type"] == "streams" and len(info["streams"]) == 5,
          str(info["streams"]))
    page = await svc.section_items("bac", "mathematiques", "", stream="se")
    check("bac/se items", len(page.items) > 15, f"{len(page.items)} items")

    # 7) الفهرسة
    print("\n== 7) indexer ==")
    db_path = os.path.join(config.TMP_DIR, "smoke_test.db")
    os.makedirs(config.TMP_DIR, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    idx = Indexer(fetcher, db_path)
    added = await idx.index_subject("bem", "mathematiques")
    check("index subject", added > 10, f"+{added} items")
    print(idx.stats_text())

    # 8) إحصائيات fetcher
    print("\n== 8) fetcher stats ==")
    print(fetcher.stats_line())

    await fetcher.close()
    idx.close()

    print(f"\n{'='*50}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


asyncio.run(main())
