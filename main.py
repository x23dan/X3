#!/usr/bin/env python3
import os
import tempfile
import asyncio
from multiprocessing import Process, Queue
from telegram import Update, Document, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MAX_OUTPUT = 40000  # أقصى عدد أحرف للطباعة مباشرة
CODE_TIMEOUT = 60  # ثواني لكل كود

# ======================== Helpers ========================

def worker(code: str, q: Queue):
    import subprocess
    import os
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name

    try:
        result = subprocess.run(
            ["python3", path],
            capture_output=True,
            text=True,
            timeout=CODE_TIMEOUT
        )
        output = (result.stdout or "") + (result.stderr or "")
        q.put(output.strip() or "✅ تم التنفيذ بدون مخرجات")
    except subprocess.TimeoutExpired:
        q.put("⏱️ انتهى وقت التنفيذ")
    except Exception as e:
        q.put(f"❌ خطأ أثناء التنفيذ: {e}")
    finally:
        os.remove(path)

async def run_code(code: str) -> str:
    q = Queue()
    p = Process(target=worker, args=(code, q))
    p.start()

    # انتظر انتهاء العملية مع timeout buffer
    p.join(CODE_TIMEOUT + 5)
    if p.is_alive():
        p.terminate()
        return "⏱️ انتهى وقت التنفيذ"

    try:
        return q.get() or "✅ تم التنفيذ بدون مخرجات"
    except Exception:
        return "❌ فشل استرجاع المخرجات"

def trim_output(output: str) -> tuple[str, str | None]:
    """
    إذا تجاوز النص MAX_OUTPUT، نحفظه في ملف مؤقت للإرسال.
    ترجع tuple: (text_to_send, file_path)
    """
    if len(output) <= MAX_OUTPUT:
        return output, None

    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(output)
    tmp.close()
    return f"📄 النتيجة طويلة جدًا، تم حفظها في ملف:", tmp.name

# ======================== Handlers ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت تنفيذ Python\n\n"
        "📌 أرسل كود Python مباشرة\n"
        "📌 أو أرسل ملف .py\n\n"
        "أوامر:\n"
        "/run → إعادة تنفيذ آخر كود\n"
        "/clear → مسح الذاكرة"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🧹 تم مسح الذاكرة")

async def handle_code(code: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_code"] = code
    output = await run_code(code)
    text, file_path = trim_output(output)

    if file_path:
        await update.message.reply_text(text)
        await update.message.reply_document(InputFile(file_path, filename="output.txt"))
        os.remove(file_path)
    else:
        await update.message.reply_text(f"📤 النتيجة:\n{text}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_code(update.message.text, update, context)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc: Document = update.message.document
    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("❌ فقط ملفات .py")
        return

    if doc.file_size > 10_000_000:  # 10 ميغا كحد أقصى الآن
        await update.message.reply_text("❌ الملف كبير جدًا")
        return

    try:
        file = await doc.get_file()
        code_bytes = await file.download_as_bytearray()
        code = code_bytes.decode(errors="ignore")
    except Exception:
        await update.message.reply_text("❌ فشل قراءة الملف")
        return

    await handle_code(code, update, context)

async def run_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data.get("last_code")
    if not code:
        await update.message.reply_text("❌ لا يوجد كود محفوظ")
        return

    await handle_code(code, update, context)

# ======================== البداية ========================

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN غير موجود")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # إضافة الأوامر والمعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("run", run_last))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    # تشغيل البوت
    app.run_polling()

if __name__ == "__main__":
    main()
