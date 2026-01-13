#!/usr/bin/env python3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    CallbackContext
)
import os
import tempfile
import subprocess
import time
import threading
import queue
from datetime import datetime
from typing import Dict, List, Optional
from collections import defaultdict

# ────────────────────────────────────────────
#               الإعدادات
# ────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("خطأ: لم يتم العثور على BOT_TOKEN")
    exit(1)

ADMIN_IDS = os.environ.get("ADMIN_IDS", "")
ADMIN_USERS = [int(x.strip()) for x in ADMIN_IDS.split(",")] if ADMIN_IDS else []

# ────────────────────────────────────────────
#               هيكل المهمة
# ────────────────────────────────────────────

class Task:
    def __init__(self, task_id: str, user_id: int, username: str, code: str):
        self.id = task_id
        self.user_id = user_id
        self.username = username
        self.code = code
        self.status = "pending"
        self.output = ""
        self.error = ""
        self.execution_time = 0.0
        self.start_time = datetime.now()

# ────────────────────────────────────────────
#               نظام تنفيذ المهام
# ────────────────────────────────────────────

class CodeExecutor:
    def __init__(self):
        self.queue = queue.Queue()
        self.tasks: Dict[str, Task] = {}
        self.history: List[Task] = []
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def add_task(self, user_id: int, username: str, code: str) -> str:
        task_id = f"t_{int(time.time()*1000)}_{user_id % 10000}"
        task = Task(task_id, user_id, username, code)
        self.tasks[task_id] = task
        self.queue.put(task)
        return task_id

    def _worker(self):
        while True:
            try:
                task = self.queue.get(timeout=10)
                self._run_task(task)
                self.history.append(task)
                if len(self.history) > 100:
                    self.history.pop(0)
            except queue.Empty:
                continue

    def _run_task(self, task: Task):
        task.status = "running"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
            tmp.write(task.code)
            path = tmp.name

        try:
            start = time.time()
            result = subprocess.run(
                ["python", "-u", path],
                capture_output=True,
                text=True,
                timeout=45,
                encoding="utf-8",
                errors="replace"
            )
            task.execution_time = time.time() - start
            task.output = result.stdout.rstrip()
            task.error  = result.stderr.rstrip()
            task.status = "done" if result.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            task.status = "timeout"
            task.error = "انتهى وقت التنفيذ (45 ثانية)"
        except Exception as e:
            task.status = "error"
            task.error = f"خطأ في النظام:\n{str(e)}"
        finally:
            try:
                os.unlink(path)
            except:
                pass

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

# ────────────────────────────────────────────
#               البوت
# ────────────────────────────────────────────

executor = CodeExecutor()

def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("كود جديد", callback_data="new")],
        [InlineKeyboardButton("مهامي",    callback_data="mine")],
    ]
    update.message.reply_text(
        "مرحباً! أرسل كود بايثون لتشغيله\n"
        "أو استخدم الأزرار أدناه",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def new_code_prompt(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    query.edit_message_text(
        "أرسل الكود الآن...\n\n"
        "يمكنك وضعه داخل ```python\nالكود\n```"
    )

def handle_code(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines[0].lower().startswith("```python"):
            text = "\n".join(lines[1:-1]).strip()
        else:
            text = "\n".join(lines[1:-1]).strip()

    if len(text) < 3:
        update.message.reply_text("الكود قصير جدًا")
        return

    task_id = executor.add_task(
        update.effective_user.id,
        update.effective_user.username or update.effective_user.first_name,
        text
    )

    update.message.reply_text(
        f"تم إضافة المهمة\n"
        f"معرف المهمة: `{task_id}`\n"
        "انتظر قليلاً...",
        parse_mode="Markdown"
    )

def status(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("اكتب: /status <معرف_المهمة>")
        return

    task = executor.get_task(context.args[0])

    if not task:
        update.message.reply_text("لم يتم العثور على المهمة")
        return

    msg = [
        f"🆔 {task.id}",
        f"الحالة: {task.status}",
        f"المستخدم: {task.username}",
        f"الوقت: {task.execution_time:.2f} ث",
    ]

    if task.output:
        msg.append("\nالمخرجات:")
        msg.append("─" * 40)
        msg.append(task.output)
        msg.append("─" * 40)

    if task.error:
        msg.append("\nالأخطاء:")
        msg.append("─" * 40)
        msg.append(task.error)
        msg.append("─" * 40)

    # بدون parse_mode لتجنب مشاكل الـ entities
    update.message.reply_text("\n".join(msg))

# ────────────────────────────────────────────
#               التشغيل
# ────────────────────────────────────────────

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_code))
    dp.add_handler(CallbackQueryHandler(new_code_prompt, pattern="^new$"))

    print("البوت يعمل...")
    updater.start_polling(drop_pending_updates=True)
    updater.idle()

if __name__ == "__main__":
    main()
