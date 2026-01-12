import subprocess
import tempfile
import os
import re
from telegram import Update
from telegram.ext import Updater, MessageHandler, Filters, CommandHandler, CallbackContext

BOT_TOKEN = os.environ.get("BOT_TOKEN")

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🤖 بوت تنفيذ بايثون جاهز\n"
        "أرسل كود Python مباشرة وسيتم تشغيله."
    )

def install_missing_modules(code: str):
    """يحاول تثبيت المكتبات المذكورة في import"""
    imports = re.findall(r'^\s*import (\w+)|^\s*from (\w+) import', code, re.MULTILINE)
    modules = set([m[0] or m[1] for m in imports])
    for module in modules:
        try:
            __import__(module)
        except ModuleNotFoundError:
            # تثبيت المكتبة تلقائيًا
            subprocess.run(
                [os.sys.executable, "-m", "pip", "install", "--user", module],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

def execute_code(update: Update, context: CallbackContext):
    code = update.message.text

    # تثبيت المكتبات الناقصة قبل التنفيذ
    install_missing_modules(code)

    # حفظ الكود في ملف مؤقت
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        result = subprocess.run(
            [os.sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=300  # 5 دقائق كحد أقصى
        )
        output = (result.stdout or "") + (result.stderr or "")
        if not output.strip():
            output = "✅ تم التنفيذ بدون مخرجات"
    except subprocess.TimeoutExpired:
        output = "⏱️ انتهى الوقت (Timeout)"
    finally:
        os.remove(script_path)

    if len(output) > 4000:
        output = output[:4000] + "\n... (تم القطع)"

    update.message.reply_text(f"📤 النتيجة:\n{output}")

def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, execute_code))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
