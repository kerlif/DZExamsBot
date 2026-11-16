"""
bot.py — بوت تيليغرام لموقع dzexams.com
مساعدة الطلبة على الوصول للدروس والفروض والاختبارات ومواضيع الشهادات.

التشغيل: python bot.py
المتطلبات في requirements.txt — الإعدادات في config.py / .env
"""
import asyncio
import datetime
import html
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from typing import Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, filters, MessageHandler, TypeHandler,
)
from telegram.request import HTTPXRequest

import config
import catalog
from fetcher import Fetcher
from warp import WarpManager
from service import ContentService, ContentError
from indexer import Indexer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dzx.bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ─── كائنات عامة ─────────────────────────────────────────────────────────
warp = WarpManager()
fetcher = Fetcher(warp)
content = ContentService(fetcher)
indexer = Indexer(fetcher)

# مخزن جلسات العرض: hash -> state dict
class SessionStore:
    def __init__(self, cap: int = 400):
        self.cap = cap
        self._d = OrderedDict()

    def put(self, value: dict) -> str:
        # مفاتيح عشوائية غير قابلة للتخمين (كانت عدّادًا متسلسلًا)
        key = secrets.token_hex(4)
        while key in self._d:
            key = secrets.token_hex(4)
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.cap:
            self._d.popitem(last=False)
        return key

    def get(self, key: str) -> Optional[dict]:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

sessions = SessionStore()


# ─── أدوات مساعدة ─────────────────────────────────────────────────────────
def esc(s: str) -> str:
    return html.escape(s or "")


def trim_btn(s: str, n: int = 58) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def stage_of_level(level: str) -> str:
    for st, (_name, levels) in catalog.STAGES.items():
        if level in levels:
            return st
    return "exams"


def stream_ar(level: str, code: str) -> str:
    """اسم الشعبة بالعربية حسب المستوى (رموز الثانوي تختلف عن البكالوريا)."""
    if level in catalog.SCHOOL_STREAMS:
        return catalog.SCHOOL_STREAMS[level].get(code, code)
    return catalog.STREAMS.get(code, code)


# ─── لوحات الأزرار ────────────────────────────────────────────────────────
def stages_markup() -> InlineKeyboardMarkup:
    rows = []
    for st, (name, _levels) in catalog.STAGES.items():
        rows.append([InlineKeyboardButton(name, callback_data=f"STG:{st}")])
    return InlineKeyboardMarkup(rows)


def levels_markup(stage: str) -> InlineKeyboardMarkup:
    _name, levels = catalog.STAGES[stage]
    rows = []
    row = []
    for lv in levels:
        row.append(InlineKeyboardButton(
            trim_btn(catalog.LEVELS[lv], 28), callback_data=f"L:{lv}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="MENU")])
    return InlineKeyboardMarkup(rows)


def streams_markup(level: str) -> InlineKeyboardMarkup:
    streams = catalog.streams_for_level(level)
    rows = []
    for code, name in streams.items():
        rows.append([InlineKeyboardButton(
            trim_btn(name, 40), callback_data=f"STR:{level}:{code}")])
    rows.append([InlineKeyboardButton(
        "🔙 رجوع", callback_data=f"STG:{stage_of_level(level)}")])
    return InlineKeyboardMarkup(rows)


def subjects_markup(level: str, page: int = 0, stream: str = "") -> InlineKeyboardMarkup:
    subs = catalog.subjects_for(level)
    per = config.SUBJECTS_PER_PAGE
    chunk = subs[page * per: (page + 1) * per]
    rows = []
    row = []
    for s in chunk:
        row.append(InlineKeyboardButton(
            trim_btn(catalog.subject_ar(s), 26),
            callback_data=f"S:{level}:{s}:{stream}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "⬅️", callback_data=f"SP:{level}:{stream}:{page-1}"))
    if len(subs) > per:
        nav.append(InlineKeyboardButton(f"{page+1}/{(len(subs)+per-1)//per}",
                                        callback_data="NOOP"))
    if (page + 1) * per < len(subs):
        nav.append(InlineKeyboardButton(
            "➡️", callback_data=f"SP:{level}:{stream}:{page+1}"))
    if nav:
        rows.append(nav)
    back_data = f"L:{level}" if stream else f"STG:{stage_of_level(level)}"
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_data)])
    return InlineKeyboardMarkup(rows)


def list_view_markup(state: dict, page: int) -> InlineKeyboardMarkup:
    items = state["items"]
    per = config.ITEMS_PER_PAGE
    total_pages = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, total_pages - 1))
    key = state["key"]
    rows = []
    for i in range(page * per, min((page + 1) * per, len(items))):
        it = items[i]
        label = trim_btn(it["title"], 56) + (" ✅" if it.get("solution") else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"IT:{key}:{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"PG:{key}:{page-1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="NOOP"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"PG:{key}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"BK:{key}")])
    rows.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="MENU")])
    return InlineKeyboardMarkup(rows)


def list_title(state: dict) -> str:
    level = state.get("level", "")
    subject = state.get("subject", "")
    section = state.get("section", "")
    stream = state.get("stream", "")
    parts = []
    if stream:
        parts.append(stream_ar(level, stream))
    elif section:
        parts.append(catalog.SECTIONS.get(section, section))
    parts.append(catalog.subject_ar(subject))
    lv_name = catalog.LEVELS.get(level, level)
    return f"📚 {esc(' — '.join(parts))}\n🎓 {esc(lv_name)}"


# ─── نصوص الواجهة ─────────────────────────────────────────────────────────
WELCOME = (
    "بوت DzExams — دروس وفروض واختبارات ومواضيع شهادات.\n\n"
    "اذا أعجبك هذا البوت لا تنسى أن تشاركه مع أصدقائك @DZExamBot "
    "لكي يستفيدو معك فالدال على الخير كفاعله✅💯\n\n"
    "من تطوير: عبد الرؤوف Dev©\n\n"
    "▪︎لا تنسوني من صالح دعائكم 💯✅❤️\n\n"
    "اختر الطور للبدء:"
)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        WELCOME, reply_markup=stages_markup(), disable_web_page_preview=True,
    )


async def menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        await q.edit_message_text(
            WELCOME, reply_markup=stages_markup(), disable_web_page_preview=True)
    except BadRequest:
        pass


# ─── التنقل: الطور ← المستوى ← الشعبة ← المادة ← القسم ← العناصر ─────────
async def cb_stage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    stage = q.data.split(":", 1)[1]
    name = catalog.STAGES.get(stage, ("", []))[0] or stage
    await q.answer()
    await q.edit_message_text(
        f"📚 {esc(name)}\n\nاختر السنة:",
        parse_mode="HTML",
        reply_markup=levels_markup(stage),
        disable_web_page_preview=True,
    )


async def cb_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    level = q.data.split(":", 1)[1]
    await q.answer()
    name = catalog.LEVELS.get(level, level)
    streams = catalog.streams_for_level(level)
    if streams:
        await q.edit_message_text(
            f"🎓 {esc(name)}\n\nاختر الشعبة:",
            parse_mode="HTML",
            reply_markup=streams_markup(level),
            disable_web_page_preview=True,
        )
        return
    await q.edit_message_text(
        f"📚 {esc(name)}\n\nاختر المادة:",
        parse_mode="HTML",
        reply_markup=subjects_markup(level),
        disable_web_page_preview=True,
    )


async def cb_stream_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, level, stream = q.data.split(":")
    await q.answer()
    name = catalog.LEVELS.get(level, level)
    sname = stream_ar(level, stream)
    await q.edit_message_text(
        f"🎓 {esc(name)} — {esc(sname)}\n\nاختر المادة:",
        parse_mode="HTML",
        reply_markup=subjects_markup(level, stream=stream),
        disable_web_page_preview=True,
    )


async def cb_subjects_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, level, stream, page = q.data.split(":")
    await q.answer()
    await q.edit_message_reply_markup(
        reply_markup=subjects_markup(level, int(page), stream))


async def cb_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":", 3)
    level, subject = parts[1], parts[2]
    stream = parts[3] if len(parts) > 3 else ""
    await q.answer("جاري التحميل…")
    await q.edit_message_text("جاري التحميل…")
    await _render_subject_screen(q, level, subject, stream)


async def _render_subject_screen(q, level: str, subject: str, stream: str = ""):
    """عرض شاشة المادة: شعب (بكالوريا) أو أقسام أو عناصر مباشرة.

    لا تجيب على الاستدعاء هنا — المتصل يجيب أولًا.
    """
    try:
        info = await content.subject_page(level, subject)
    except ContentError as e:
        await _err_edit(q, e)
        return
    except Exception:
        log.exception("subject_page failed")
        await _err_edit(q, ContentError("حدث خطأ بسيط. حاول مجددًا."))
        return

    sub_name = catalog.subject_ar(subject)
    lv_name = catalog.LEVELS.get(level, level)
    back_data = f"L:{level}" if stream else f"STG:{stage_of_level(level)}"

    if info["type"] == "streams":
        rows = []
        for st in info["streams"]:
            rows.append([InlineKeyboardButton(
                trim_btn(catalog.STREAMS.get(st, st), 40),
                callback_data=f"ST:{subject}:{st}")])
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_data)])
        await q.edit_message_text(
            f"📚 {esc(sub_name)}\n🎓 {esc(lv_name)}\n\nاختر الشعبة:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if info["type"] == "exam" and info["items"] and info["items"].items:
        await _show_items(q, level, subject,
                          section="", stream=stream, items=info["items"].items)
        return

    if info["type"] == "school" and info["sections"]:
        rows = []
        for sec in info["sections"]:
            rows.append([InlineKeyboardButton(
                trim_btn(catalog.SECTIONS.get(sec, sec), 40),
                callback_data=f"SEC:{level}:{subject}:{sec}:{stream}")])
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_data)])
        await q.edit_message_text(
            f"📚 {esc(sub_name)}\n🎓 {esc(lv_name)}\n\nاختر القسم:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    await q.edit_message_text(
        f"لا يوجد محتوى بعد في مادة {esc(sub_name)}.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 رجوع", callback_data=back_data)]]))


async def cb_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, subject, stream = q.data.split(":")
    await q.answer("جاري التحميل…")
    await q.edit_message_text("جاري التحميل…")
    try:
        page = await content.section_items("bac", subject, "", stream=stream)
    except ContentError as e:
        await _err_edit(q, e)
        return
    await _show_items(q, "bac", subject, section="",
                      stream=stream, items=page.items)


async def cb_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":", 4)
    level, subject, section = parts[1], parts[2], parts[3]
    stream = parts[4] if len(parts) > 4 else ""
    await q.answer("جاري التحميل…")
    await q.edit_message_text("جاري التحميل…")
    try:
        page = await content.section_items(level, subject, section)
    except ContentError as e:
        await _err_edit(q, e)
        return
    await _show_items(q, level, subject, section=section,
                      stream=stream, items=page.items)


# ─── عرض قائمة العناصر ────────────────────────────────────────────────────
_SCHOOL_STREAM_CODES = {"se", "m", "tm", "al", "ge", "tcst", "tcl"}


def _item_order(d: dict):
    """مفتاح ترتيب العناصر: السنة تنازليًا (الأحدث أولًا)، ثم رقم
    النموذج تصاعديًا داخل السنة، ثم العنوان. يثبّت الترتيب مهما
    تغيّر ترتيب صفحة الموقع بين طلب وآخر."""
    try:
        year = int(d.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    m = re.search(r"(\d+)", d.get("title") or "")
    num = int(m.group(1)) if m else 0
    return (-year, num, d.get("title") or "")


async def _show_items(q, level: str, subject: str,
                      section: str, stream: str, items):
    dicts = content.items_to_dicts(items)
    if stream in _SCHOOL_STREAM_CODES:
        # الشعبة ترتّب ولا تُخفي: ملفات الشعبة (أو المشتركة بلا وسوم) أولًا،
        # ثم بقية ملفات المادة كما يعرضها الموقع. الفلترة الصارمة كانت
        # تُخفي 53 من 56 ملفًا (علوم الطبيعة 3 ثانوي، شعبة رياضيات).
        dicts.sort(key=lambda d: (
            0 if (not d.get("tags") or stream in d.get("tags", [])) else 1,
            *_item_order(d)))
    else:
        dicts.sort(key=_item_order)
    if not dicts:
        back_data = f"S:{level}:{subject}:{stream}"
        empty_text = ("لا توجد مستندات لهذه الشعبة في هذا القسم."
                      if stream else "لا توجد مستندات في هذا القسم حاليًا.")
        await q.edit_message_text(
            empty_text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 رجوع", callback_data=back_data)
            ]]))
        return

    state = {"level": level, "subject": subject, "section": section,
             "stream": stream, "items": dicts}
    key = sessions.put(state)
    state["key"] = key
    title = list_title(state)
    text = (f"{title}\n\nعدد المستندات: {len(dicts)}\n"
            f"اضغط على أي مستند لعرضه.")
    await q.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=list_view_markup(state, 0),
        disable_web_page_preview=True)


async def cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key, page = q.data.split(":")
    state = sessions.get(key)
    if not state:
        await q.answer("انتهت هذه القائمة. ابدأ من جديد بـ /start.",
                       show_alert=True)
        return
    await q.answer()
    state["page"] = int(page)
    await q.edit_message_reply_markup(
        reply_markup=list_view_markup(state, int(page)))


async def _back_to_list_caption(q, state: dict):
    """إعادة قائمة العناصر في تعليق رسالة الوسائط (ملف PDF).

    رسالة الوثيقة لها تعليق لا نص، فلا يصح edit_message_text عليها.
    """
    text = (f"{list_title(state)}\n\nعدد المستندات: {len(state['items'])}\n"
            f"اضغط على أي مستند لعرضه.")
    markup = list_view_markup(state, state.get("page", 0))
    try:
        await q.edit_message_caption(caption=text, parse_mode="HTML",
                                     reply_markup=markup)
    except BadRequest:
        # التعليق مطابق أو غير قابل للتعديل — أرسل القائمة رسالة جديدة.
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass
        await q.message.reply_text(text, parse_mode="HTML",
                                   reply_markup=markup,
                                   disable_web_page_preview=True)


async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرجوع من قائمة العناصر إلى الشاشة الأعلى (مواد/أقسام/شعب).

    يُعاد بناء الشاشة الأعلى من حالة الجلسة المخزنة — لا يُعدَّل
    q.data أبدًا (PTB 21 يجمّد CallbackQuery).
    """
    q = update.callback_query
    _, key = q.data.split(":")
    state = sessions.get(key)
    if not state:
        await q.answer("انتهت هذه القائمة. ابدأ من جديد بـ /start.",
                       show_alert=True)
        return
    # زر الرجوع المرفق مع ملف PDF: الرسالة وثيقة (لها تعليق لا نص)،
    # فتعديل النص يفشل — نعيد القائمة نفسها في التعليق.
    if q.message and (q.message.document or q.message.photo
                      or q.message.video):
        await q.answer()
        await _back_to_list_caption(q, state)
        return
    await q.answer()
    level = state["level"]
    subject = state["subject"]
    stream = state.get("stream", "")
    if level in catalog.EXAM_LEVELS and not state.get("section") and not stream:
        await q.edit_message_text(
            f"📚 {esc(catalog.LEVELS.get(level, level))}\n\nاختر المادة:",
            parse_mode="HTML",
            reply_markup=subjects_markup(level),
            disable_web_page_preview=True)
        return
    await _render_subject_screen(q, level, subject, stream)


# ─── اختيار عنصر وإرسال PDF ──────────────────────────────────────────────
async def cb_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key, idx = q.data.split(":")
    state = sessions.get(key)
    if not state:
        await q.answer("انتهت هذه القائمة. ابدأ من جديد بـ /start.",
                       show_alert=True)
        return
    items = state["items"]
    i = int(idx)
    if i >= len(items):
        await q.answer("العنصر غير متوفر.", show_alert=True)
        return
    it = items[i]
    # حد التحميل لكل مستخدم: مهلة بين ملف وآخر (حماية من إغراق الموقع)
    now = time.monotonic()
    last = context.chat_data.get("last_dl_ts", 0.0)
    wait = int(config.USER_DL_COOLDOWN - (now - last))
    if wait > 0:
        await q.answer(f"انتظر {wait} ثوانٍ ثم أعد المحاولة.",
                       show_alert=True)
        return
    context.chat_data["last_dl_ts"] = now
    await q.answer("جاري التحميل…")

    item_obj = _dict_to_item(it)
    chat_id = q.message.chat_id
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)

    pdf_path = None
    pdf_url = ""
    detail_title = it["title"]
    try:
        detail = await content.item_detail(item_obj)
        pdf_url = detail.get("pdf_url", "")
        if detail.get("title"):
            detail_title = detail["title"]
        if pdf_url:
            pdf_path = await content.download_item_pdf(item_obj)
    except ContentError as e:
        kb = _item_markup(it, key, i, site=True)
        await context.bot.send_message(
            chat_id, e.msg_ar, reply_markup=kb, parse_mode="HTML")
        return
    except Exception:
        log.exception("item fetch failed")
        kb = _item_markup(it, key, i, site=True)
        await context.bot.send_message(
            chat_id, "حدث خطأ بسيط. حاول مجددًا.",
            reply_markup=kb, parse_mode="HTML")
        return

    # الحل في نفس ملف PDF حسب الموقع — نكتفي بالإشارة من علامة القائمة.
    has_solution = bool(it.get("solution"))
    caption = (
        f"📄 <b>{esc(trim_btn(detail_title, 120))}</b>\n"
        f"🎓 {esc(catalog.LEVELS.get(it['level'], it['level']))}"
        f" | {esc(catalog.subject_ar(it['subject']))}"
        + (f" | سنة {esc(it['year'])}" if it.get("year") else "")
        + ("\n✅ الحل مرفق مع الملف." if has_solution else "\nالحل غير متوفر لهذا الملف.")
    )

    # حد تيليغرام: ملف أكبر من 49MB يُرسل كرابط لا كمستند
    oversize = False
    if pdf_path:
        try:
            if os.path.getsize(pdf_path) > config.TELEGRAM_DOC_LIMIT:
                oversize = True
        except OSError:
            pass
        if oversize:
            try:
                os.remove(pdf_path)
            except OSError:
                pass
            pdf_path = None

    if pdf_path:
        with open(pdf_path, "rb") as fh:
            await context.bot.send_document(
                chat_id=chat_id,
                document=fh,
                filename=_pdf_filename(detail_title),
                caption=caption,
                parse_mode="HTML",
                reply_markup=_item_markup(it, key, i, site=True),
            )
        try:
            os.remove(pdf_path)
        except OSError:
            pass
    else:
        kb = _item_markup(it, key, i, site=True)
        if oversize:
            note = ("الملف كبير (أكثر من 49MB) — لا يمكن إرساله عبر تيليغرام.\n"
                    "افتحه من زر الموقع بالأسفل.")
        else:
            note = ("لم أستطع تنزيل ملف PDF مباشرة.\n"
                    "افتحه من زر الموقع بالأسفل.")
        await context.bot.send_message(
            chat_id, note, reply_markup=kb, parse_mode="HTML")


def _dict_to_item(d: dict):
    from scraper import Item
    tags = d.get("tags", [])
    if isinstance(tags, str):
        tags = [t for t in tags.split(",") if t]
    return Item(
        kind=d.get("kind", "sujets"), token=d.get("token", ""),
        title=d.get("title", ""), url=d.get("url", ""),
        level=d.get("level", ""), subject=d.get("subject", ""),
        section=d.get("section", ""), stream=d.get("stream", ""),
        year=d.get("year", ""), solution=bool(d.get("solution")),
        tags=list(tags or []),
    )


def _item_markup(it: dict, key: str, idx: int,
                 site: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if site and it.get("url"):
        rows.append([InlineKeyboardButton("🌐 فتح في الموقع",
                                          url=config.BASE_URL + it["url"])])
    rows.append([InlineKeyboardButton("🔙 رجوع للقائمة", callback_data=f"BK:{key}")])
    return InlineKeyboardMarkup(rows)


def _pdf_filename(title: str) -> str:
    t = re.sub(r"[^\w\u0600-\u06FF -]", "", title).strip()[:80]
    return f"{t or 'dzexams'}.pdf"


async def _err_edit(q, e: ContentError):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌐 فتح الموقع", url=config.BASE_URL + "/ar"),
        InlineKeyboardButton("🏠 القائمة", callback_data="MENU"),
    ]])
    try:
        await q.edit_message_text(esc(e.msg_ar), parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        pass


async def _on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة نصية عادية: تذكير بالأوامر."""
    msg = update.effective_message
    if not (msg.text or "").strip():
        return
    await msg.reply_text("اكتب /start للبدء، أو اختر من القائمة.")


async def _record_user_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل كل مستخدم يتفاعل مع البوت + إخطار المشرف بالمستخدم الجديد.

    يعمل في مجموعة -1 قبل كل المعالجات، فلا يفوّت أي مستخدم جديد
    مهما كانت أول رسالة منه (/start أو زر أو نص).
    """
    user = update.effective_user
    if not user or user.is_bot:
        return
    try:
        is_new = indexer.record_user(
            user.id, user.username or "",
            user.first_name or "", user.last_name or "")
    except Exception:
        log.exception("record_user failed")
        return
    if not is_new or user.id in config.ADMIN_IDS:
        return
    name = " ".join(
        part for part in (user.first_name, user.last_name) if part) or "—"
    username = f"@{user.username}" if user.username else "لا يوجد"
    text = ("👤 مستخدم جديد للبوت:\n"
            f"الاسم: {name}\n"
            f"المعرّف: {user.id}\n"
            f"اليوزر: {username}")
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError:
            log.warning("notify admin %s failed", admin_id)


# ─── أوامر المشرفين ──────────────────────────────────────────────────────
def is_admin(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid in config.ADMIN_IDS


async def _admin_only(update: Update) -> bool:
    if not is_admin(update):
        await update.effective_message.reply_text("هذا الأمر للمشرفين فقط.")
        return False
    return True


async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    await update.effective_message.reply_text(
        f"🌐 {await warp.describe()}\n\n📊 {fetcher.stats_line()}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    await update.effective_message.reply_text(indexer.stats_text())


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عدد مستخدمي البوت — للمشرف فقط (غير ظاهر في القائمة)."""
    if not await _admin_only(update):
        return
    n = indexer.count_users(exclude=config.ADMIN_IDS)
    await update.effective_message.reply_text(f"👥 عدد مستخدمي البوت: {n}")


async def cmd_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    level = (context.args[0].lower() if context.args else "").strip()
    if not catalog.is_valid_level(level):
        await update.effective_message.reply_text(
            "الاستخدام: /index <مستوى>\nالمستويات: " +
            ", ".join(catalog.LEVELS.keys()))
        return
    if indexer.progress.get("running"):
        await update.effective_message.reply_text(
            "توجد فهرسة جارية بالفعل. تفقّد /idxstatus أو أوقفها /idxstop")
        return
    msg = await update.effective_message.reply_text(
        f"بدأت فهرسة مستوى {level} في الخلفية…")
    task = asyncio.create_task(_run_indexing(level, msg.chat_id, context))
    context.application.bot_data["index_task"] = task


async def cmd_indexall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    if indexer.progress.get("running"):
        await update.effective_message.reply_text("توجد فهرسة جارية.")
        return
    msg = await update.effective_message.reply_text(
        "بدأت فهرسة كل المستويات — العملية طويلة وتجري في الخلفية.")
    task = asyncio.create_task(_run_indexing_all(msg.chat_id, context))
    context.application.bot_data["index_task"] = task


async def _run_indexing(level: str, chat_id: int, context):
    try:
        await indexer.index_level(level)
    except Exception as e:
        log.exception("indexing failed: %s", e)
        indexer.progress["running"] = False
        indexer.progress["last_error"] = str(e)
    await _final_report(chat_id, context)


async def _run_indexing_all(chat_id: int, context):
    try:
        for lv in catalog.LEVELS:
            await indexer.index_level(lv)
    except Exception as e:
        log.exception("indexall failed: %s", e)
        indexer.progress["running"] = False
    await _final_report(chat_id, context)


async def _final_report(chat_id: int, context):
    try:
        await context.bot.send_message(
            chat_id,
            "اكتملت الفهرسة.\n\n" + indexer.stats_text())
    except TelegramError:
        pass


async def cmd_idxstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    await update.effective_message.reply_text(indexer.progress_text())


async def cmd_idxstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    task = context.application.bot_data.get("index_task")
    if task and not task.done():
        task.cancel()
        indexer.progress["running"] = False
        await update.effective_message.reply_text("أوقفت الفهرسة.")
    else:
        await update.effective_message.reply_text("لا توجد فهرسة جارية.")


# ─── الفهرسة التلقائية اليومية ────────────────────────────────────────────
async def auto_index_job(context: ContextTypes.DEFAULT_TYPE):
    if not config.AUTO_INDEX_LEVELS:
        return
    if indexer.progress.get("running"):
        return
    for lv in config.AUTO_INDEX_LEVELS:
        if catalog.is_valid_level(lv):
            try:
                await indexer.index_level(lv)
            except Exception:
                log.exception("auto index %s failed", lv)


# ─── راوتر الاستدعاءات ────────────────────────────────────────────────────
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    if data == "NOOP":
        await q.answer()
        return
    route = data.split(":", 1)[0]
    handlers = {
        "MENU": menu_cb,
        "STG": cb_stage,
        "L": cb_level,
        "STR": cb_stream_select,
        "SP": cb_subjects_page,
        "S": cb_subject,
        "SEC": cb_section,
        "ST": cb_stream,
        "PG": cb_page,
        "IT": cb_item,
        "BK": cb_back,
    }
    handler = handlers.get(route)
    if handler:
        try:
            await handler(update, context)
        except TelegramError as e:
            log.warning("telegram error in %s: %s", route, e)
            try:
                await q.answer("حدث خطأ بسيط. حاول مجددًا.", show_alert=True)
            except Exception:
                pass
        except Exception:
            log.exception("handler %s crashed", route)
            try:
                await q.answer("حدث خطأ بسيط. حاول مجددًا.", show_alert=True)
            except Exception:
                pass
    else:
        await q.answer()


async def _on_error(update, context):
    """معالج أخطاء عام: يسجّل الخطأ ويرد على المستخدم برسالة قصيرة."""
    log.exception("update failed: %s", context.error)
    if update is None:
        return
    try:
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "حدث خطأ بسيط. حاول مجددًا.", show_alert=True)
            except Exception:
                pass
        elif update.effective_message:
            await update.effective_message.reply_text(
                "حدث خطأ بسيط. حاول مجددًا.")
    except Exception:
        pass


async def _post_stop(app):
    """إغلاق جلسة HTTP عند إيقاف البوت (لا تترك جلسة مفتوحة)."""
    try:
        await fetcher.close()
    except Exception:
        pass


async def _post_init(app):
    """قائمة الأوامر الظاهرة للمستخدمين: أوامر المستخدم فقط."""
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "القائمة الرئيسية"),
        ])
    except TelegramError:
        log.warning("set_my_commands failed")


# ─── التشغيل ──────────────────────────────────────────────────────────────
def main():
    if not config.BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN غير مضبوط. ضعه في ملف .env أو متغيرات البيئة.")
    os.makedirs(config.TMP_DIR, exist_ok=True)

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .request(HTTPXRequest(
            connect_timeout=20.0,
            read_timeout=60.0,
            write_timeout=60.0,
            pool_timeout=10.0,
        ))
        .post_init(_post_init)
        .post_stop(_post_stop)
        .build()
    )
    app.add_error_handler(_on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("index", cmd_index))
    app.add_handler(CommandHandler("indexall", cmd_indexall))
    app.add_handler(CommandHandler("idxstatus", cmd_idxstatus))
    app.add_handler(CommandHandler("idxstop", cmd_idxstop))
    app.add_handler(CommandHandler("ip", cmd_ip))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("list", cmd_list))
    # تسجيل المستخدمين قبل كل شيء (مجموعة -1، لا يحجب غيره)
    app.add_handler(TypeHandler(Update, _record_user_update, block=False),
                    group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   _on_text_message))
    app.add_handler(CallbackQueryHandler(cb_router))

    # الفهرسة التلقائية اليومية
    if config.AUTO_INDEX_LEVELS:
        try:
            app.job_queue.run_daily(
                auto_index_job,
                time=datetime.time(hour=config.AUTO_INDEX_HOUR, minute=0),
                name="auto_index",
            )
            log.info("auto-index scheduled daily at %02d:00 for %s",
                     config.AUTO_INDEX_HOUR, config.AUTO_INDEX_LEVELS)
        except AttributeError:
            log.warning("job-queue غير متاح: ثبّت python-telegram-bot[job-queue]")

    log.info("Starting DzExams bot…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    finally:
        # إغلاق نظيف
        pass
