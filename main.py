import subprocess
import tempfile
import os
from telegram import Update
from telegram.ext import Updater, MessageHandler, Filters, CommandHandler, CallbackContext

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))

def start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    update.message.reply_text(
        "🤖 بوت تنفيذ بايثون جاهز\n"
        "أرسل كود Python مباشرة وسيتم تشغيله."
    )

def execute_code(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return

    code = update.message.text

    # حفظ الكود في ملف مؤقت
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        result = subprocess.run(
            ["python", script_path],
            capture_output=True,
            text=True,
            timeout=300
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
