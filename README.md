# DZExamsBot

بوت تيليغرام يقرّب محتوى موقع [dzexams.com](https://www.dzexams.com) للتلاميذ الجزائريين: دروس، فروض، اختبارات، ومواضيع شهادات (BEM / BAC)، بتصفح بسيط بالأزرار وبحث نصي.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template?referralCode=dzexamsbot&repo=https://github.com/kerlif/DZExamsBot)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kerlif/DZExamsBot)

## ماذا يفعل

- تصفح هرمي: الطور (ابتدائي / متوسط / ثانوي / شهادات) ← السنة ← الشعبة (ثانوي وشهادات) ← المادة ← القسم (دروس، فروض، اختبارات…) ← الملفات.
- الشعبة **ترتّب ولا تُخفي**: ملفات شعبتك أولًا، ثم بقية ملفات المادة — كما يعرضها الموقع.
- ترتيب الملفات: الأحدث سنة أولًا، ثم رقم النموذج.
- ✅ بجانب الملف تعني أن الحل مرفق مع نفس الملف (الحل داخل الـ PDF، لا صفحة منفصلة).
- إرسال الـ PDF مباشرة عند الضغط على الملف، مع زر «رجوع» يعيدك لنفس صفحة القائمة.
- فهرسة خلفية يومية تلقائية، وفهرسة يدوية للمشرف: `/indexall`.

## المتطلبات

- Python 3.12+
- توكن بوت تيليغرام (من [@BotFather](https://t.me/BotFather))
- معرف تيليغرام الخاص بك (لأوامر المشرف)

## التشغيل محليًا

```bash
git clone https://github.com/kerlif/DZExamsBot.git
cd DZExamsBot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# ضع BOT_TOKEN و ADMIN_IDS في .env
python bot.py
```

## متغيرات البيئة

| المتغير | الوصف | الافتراضي |
|---|---|---|
| `BOT_TOKEN` | توكن البوت | — (إلزامي) |
| `ADMIN_IDS` | معرفات المشرفين (مفصولة بفاصلة) | — |
| `DB_PATH` | مسار قاعدة البيانات SQLite | `dzexams.db` |
| `TMP_DIR` | مجلد الملفات المؤقتة | `tmp/` |
| `PROXY_LIST` | بروكسيات HTTP للخروج (اختياري، فاصلة) | — |
| `AUTO_INDEX_LEVELS` | الأطوار المفهرسة تلقائيًا يوميًا | — (معطلة إن لم تُضبط) |
| `AUTO_INDEX_HOUR` | ساعة الفهرسة التلقائية | `3` |

## الاستضافة

البوت عملية دائمة (polling) مع قاعدة SQLite محلية — يحتاج منصة تشغّل عملية مستمرة وتحتفظ بملف قاعدة البيانات.

| المنصة | الملفات الجاهزة | ملاحظات |
|---|---|---|
| **Railway** | `railway.json` | زر النشر أعلاه. أنشئ Volume عند `/app/data` واضبط `DB_PATH=/app/data/dzexams.db` و`TMP_DIR=/app/data/tmp`. متغيرات `BOT_TOKEN` و`ADMIN_IDS` من لوحة التحكم. |
| **Render** | `render.yaml` | زر النشر أعلاه. الخدمة Worker + قرص 1GB عند `/app/data`. خطة Starter المدفوعة مطلوبة للـ Worker. |
| **Fly.io** | `fly.toml` + `Dockerfile` | `fly launch --no-deploy` ثم `fly volumes create dzexams_data -s 1` ثم `fly secrets set BOT_TOKEN=… ADMIN_IDS=…` ثم `fly deploy`. |
| **Heroku** | `Procfile` + `runtime.txt` + `app.json` | يعمل كـ worker dyno. قرص Heroku مؤقت: قاعدة البيانات تُفقد عند إعادة التشغيل — أعد الفهرسة بـ `/indexall` بعد كل إعادة نشر. |
| **VPS خاص** | `systemd/dzexams-bot.service` | أنشئ المستخدم `botuser` وملّكه المجلد (`chown -R botuser:botuser /opt/dzexams-bot`)، انسخ الملف إلى `/etc/systemd/system/`، عدّل المسارات والتوكن، ثم `systemctl enable --now dzexams-bot`. الخدمة تعمل بلا صلاحيات root. |

**Cloudflare Workers غير مدعومة بهذه النسخة**: الـ Workers لا تشغّل عملية دائمة (polling مستحيل) ولا قرصًا محليًا لقاعدة SQLite — استضافتها تتطلب إعادة كتابة البوت على نمط webhook مع قاعدة خارجية، وهذا خارج نطاق هذا الإصدار.

## الأوامر

| الأمر | لمن | ماذا يفعل |
|---|---|---|
| `/start` | الجميع | القائمة الرئيسية |
| `/index` | المشرف | فهرسة المستوى الحالي |
| `/indexall` | المشرف | فهرسة كل الأطوار (~15–25 دقيقة) |
| `/idxstatus` | المشرف | حالة الفهرسة |
| `/idxstop` | المشرف | إيقاف الفهرسة |
| `/ip` | المشرف | معلومات الشبكة والبروكسي |
| `/status` | المشرف | حالة البوت |
| `/list` | المشرف | عدد مستخدمي البوت |

## كيف يصل البوت إلى الموقع

- الموقع محمي بـ Cloudflare. البوت يصل إليه بعميل HTTP عادي مع User-Agent `Mediapartners-Google` (ثغرة معروفة في الحماية) — إذا أغلقها الموقع يتوقف البوت عن الجلب ويُظهر رسالة خطأ واضحة، لا يفشل بصمت.
- المحتوى يُخزَّن كبيانات وصفية + روابط فقط؛ الـ PDF يُجلب فقط عند طلب مستخدم محدد، ولا يُنزَّل جماعيًا.
- ملفات ≥ 49MB تُرسل كرابط فقط (حد تيليغرام 50MB للبوتات) — يُفحص الحجم قبل الإرسال.
- مهلة 10 ثوانٍ بين تحميلين لنفس المستخدم (حماية من إغراق الموقع).
- سلسلة User-Agent احتياطية: إن حُجب الأساسي (Mediapartners-Google) يُجرَّب AdsBot ثم Googlebot.
- كل مستخدم جديد يُسجَّل تلقائيًا، ويصل المشرف إخطار باسمه ومعرّفه ويوزره؛ `/list` يعرض عدد المستخدمين (للمشرف فقط، ولا يحسب المشرفين).

## بنية المشروع

```
bot.py                 # واجهة تيليغرام (الأزرار، الأوامر، الجلسات)
catalog.py             # الأطوار، السنوات، الشعب، المواد
config.py              # إعدادات البيئة
service.py             # منطق التصفح والجلب
scraper.py             # تحليل صفحات HTML
fetcher.py             # عميل HTTP مع التخزين المؤقت
indexer.py             # الفهرسة الخلفية وبحث FTS5
dzenc.py               # فك ترميز روابط الموقع
stealth_browser.py     # متصفح احتياطي (غير مستخدم حاليًا)
warp.py                # تدوير IP عبر WARP/البروكسي عند تكرار الحجب (يستدعيه fetcher)
smoke_test.py          # اختبار حي شامل (15 اختبارًا)
Dockerfile             # حاوية Docker
fly.toml               # نشر Fly.io
railway.json           # نشر Railway
render.yaml            # نشر Render
Procfile / runtime.txt / app.json  # نشر Heroku
.github/workflows/ci.yml          # فحص أسبوعي (ruff, bandit, pip-audit)
systemd/dzexams-bot.service        # تشغيل VPS
```

## الاختبارات

```bash
python smoke_test.py
```

اختبار حي ضد الموقع (جلب، تصفح، PDF، شهادات، فهرسة وبحث). يحتاج اتصالًا بالموقع.

## حدود معروفة

- الوصول يعتمد على User-Agent واحد — نقطة فشل واحدة إن أغلقها الموقع.
- بعض الملفات لا تتوفر على الموقع نفسه (صفحتها ترجع 403) — يظهر خطأ واضح.
- الفهرسة الكاملة لكل الأطوار تستغرق ~15–25 دقيقة وتُشغَّل يدويًا بـ `/indexall`.

## شكر

شكر خاص لمطوري موقع [dzexams.com](https://www.dzexams.com) على المحتوى التعليمي الذي يقدمونه للتلاميذ الجزائريين — هذا البوت مجرد واجهة أسهل للوصول إلى محتواهم، وكل الحقوق محفوظة لهم.

من تطوير: عبد الرؤوف Dev© — لا تنسوه من صالح دعائكم.
