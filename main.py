#!/usr/bin/env python3
import os
import tempfile
import subprocess
import re
import time
import json
import threading
import queue
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, MessageHandler, Filters, CommandHandler, CallbackContext,
    CallbackQueryHandler
)

# ============ تهيئة المتغيرات للعمل على Railway ============
# على Railway، يتم تمرير التوكن عبر متغير البيئة
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# للمشرفين - يمكن إضافة معرفاتهم عبر متغير البيئة
ADMIN_IDS = os.environ.get("ADMIN_IDS", "")
ADMIN_USERS = []
if ADMIN_IDS:
    try:
        ADMIN_USERS = [int(id.strip()) for id in ADMIN_IDS.split(",")]
    except:
        ADMIN_USERS = []

# إعدادات أخرى
PORT = int(os.environ.get("PORT", 8443))  # Railway يستخدم PORT
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # رابط Webhook إذا كان موجود

# ============ هياكل البيانات ============
TASK_HISTORY_SIZE = 50  # تقليل الحجم لتوفير الذاكرة

class Task:
    """فئة تمثل مهمة تنفيذ كود"""
    def __init__(self, task_id: str, user_id: int, code: str):
        self.id = task_id
        self.user_id = user_id
        self.username = ""
        self.code = code
        self.status = "pending"
        self.result = ""
        self.start_time = None
        self.end_time = None
        self.execution_time = 0
        self.output = ""
        self.error = ""
        
    def to_dict(self):
        """تحويل المهمة إلى قاموس"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'code': self.code[:50] + "..." if len(self.code) > 50 else self.code,
            'status': self.status,
            'start_time': str(self.start_time) if self.start_time else None,
            'end_time': str(self.end_time) if self.end_time else None,
            'execution_time': self.execution_time,
            'has_output': bool(self.output),
            'has_error': bool(self.error)
        }

class CodeExecutorBot:
    """البوت الرئيسي مع إدارة المهام"""
    
    def __init__(self):
        self.task_queue = queue.Queue()
        self.tasks: Dict[str, Task] = {}
        self.task_history: List[Task] = []
        self.user_stats = defaultdict(lambda: {'tasks': 0, 'success': 0, 'errors': 0})
        self.system_stats = {
            'total_tasks': 0,
            'successful_tasks': 0,
            'failed_tasks': 0,
            'total_execution_time': 0
        }
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._task_worker, daemon=True)
        self.worker_thread.start()
    
    def add_task(self, user_id: int, username: str, code: str) -> str:
        """إضافة مهمة جديدة للتنفيذ"""
        task_id = f"task_{int(time.time())}_{user_id}"
        task = Task(task_id, user_id, code)
        task.username = username
        task.start_time = datetime.now()
        task.status = "pending"
        
        self.tasks[task_id] = task
        self.task_queue.put(task)
        self.user_stats[user_id]['tasks'] += 1
        self.system_stats['total_tasks'] += 1
        
        return task_id
    
    def _task_worker(self):
        """العامل الذي ينفذ المهام من الطابور"""
        while self.is_running:
            try:
                task = self.task_queue.get(timeout=1)
                self._execute_task(task)
                
                # تحديث التاريخ
                self.task_history.append(task)
                if len(self.task_history) > TASK_HISTORY_SIZE:
                    self.task_history.pop(0)
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error in task worker: {e}")
    
    def _execute_task(self, task: Task):
        """تنفيذ مهمة محددة"""
        task.status = "running"
        
        # حفظ الكود في ملف مؤقت
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(task.code)
            script_path = f.name
        
        try:
            start_time = time.time()
            result = subprocess.run(
                [os.sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=30,  # 30 ثانية فقط على Railway لتوفير الموارد
                encoding='utf-8',
                errors='ignore'
            )
            execution_time = time.time() - start_time
            
            task.execution_time = execution_time
            task.output = result.stdout
            task.error = result.stderr
            task.status = "completed"
            task.end_time = datetime.now()
            
            self.user_stats[task.user_id]['success'] += 1
            self.system_stats['successful_tasks'] += 1
            
        except subprocess.TimeoutExpired:
            task.status = "failed"
            task.error = "⏱️ انتهى وقت التنفيذ (30 ثانية كحد أقصى)"
            task.end_time = datetime.now()
            
            self.user_stats[task.user_id]['errors'] += 1
            self.system_stats['failed_tasks'] += 1
            
        except Exception as e:
            task.status = "failed"
            task.error = f"❌ خطأ أثناء التنفيذ:\n{str(e)}"
            task.end_time = datetime.now()
            
            self.user_stats[task.user_id]['errors'] += 1
            self.system_stats['failed_tasks'] += 1
            
        finally:
            try:
                os.remove(script_path)
            except:
                pass
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """الحصول على مهمة بواسطة المعرف"""
        return self.tasks.get(task_id)
    
    def get_user_tasks(self, user_id: int) -> List[Task]:
        """الحصول على مهام مستخدم معين"""
        return [task for task in self.task_history if task.user_id == user_id][-5:]  # آخر 5 مهام
    
    def get_recent_tasks(self, limit: int = 5) -> List[Task]:
        """الحصول على أحدث المهام"""
        return list(reversed(self.task_history[-limit:]))

# إنشاء نسخة من البوت
bot = CodeExecutorBot()

# ============ دوال المعالجة للتيليجرام ============

def start(update: Update, context: CallbackContext):
    """معالج أمر /start"""
    user = update.effective_user
    
    keyboard = [
        [InlineKeyboardButton("🚀 تشغيل كود جديد", callback_data='new_code')],
        [InlineKeyboardButton("📋 مهامي الأخيرة", callback_data='my_tasks')],
    ]
    
    # إضافة لوحة التحكم للمشرفين
    if user.id in ADMIN_USERS:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data='dashboard')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        f"👋 مرحباً {user.first_name}!\n"
        "🤖 بوت تنفيذ كود Python\n"
        "🚀 يعمل على Railway\n\n"
        "اختر أحد الخيارات:",
        reply_markup=reply_markup
    )

def handle_code_input(update: Update, context: CallbackContext):
    """معالجة إدخال الكود"""
    user = update.effective_user
    code = update.message.text
    
    # تجاهل الأوامر
    if code.startswith('/'):
        return
    
    # إذا كان الكود محاطًا بعلامات ```
    if code.startswith('```') and code.endswith('```'):
        code = code[3:-3].strip()
        if code.startswith('python'):
            code = code[6:].strip()
    
    # إضافة المهمة للطابور
    task_id = bot.add_task(user.id, user.username or user.first_name, code)
    
    # إرسال رسالة تأكيد
    update.message.reply_text(
        f"✅ تم إضافة المهمة للتنفيذ\n"
        f"🆔 معرف المهمة: `{task_id}`\n"
        f"⏳ جاري التنفيذ في الخلفية...\n\n"
        f"يمكنك متابعة حالة المهمة باستخدام:\n`/status {task_id}`",
        parse_mode='Markdown'
    )

def status_command(update: Update, context: CallbackContext):
    """عرض حالة مهمة معينة"""
    if not context.args:
        update.message.reply_text("⚠️ يرجى تحديد معرف المهمة:\n`/status task_1234567890`", parse_mode='Markdown')
        return
    
    task_id = context.args[0]
    task = bot.get_task(task_id)
    
    if not task:
        update.message.reply_text("❌ لم يتم العثور على المهمة")
        return
    
    user = update.effective_user
    if task.user_id != user.id and user.id not in ADMIN_USERS:
        update.message.reply_text("⛔ ليس لديك صلاحية عرض هذه المهمة")
        return
    
    status_icons = {
        'pending': '⏳',
        'running': '🔄',
        'completed': '✅',
        'failed': '❌'
    }
    
    status_text = f"""
📋 **معلومات المهمة**

🆔 **المعرف:** `{task.id}`
👤 **المستخدم:** {task.username}
📅 **وقت البدء:** {task.start_time.strftime('%Y-%m-%d %H:%M:%S') if task.start_time else 'N/A'}
{status_icons.get(task.status, '❓')} **الحالة:** {task.status}

⏱️ **زمن التنفيذ:** {task.execution_time:.2f} ثانية
"""
    
    if task.status == 'completed':
        if task.output:
            output_preview = task.output[:300] + ("..." if len(task.output) > 300 else "")
            status_text += f"\n📤 **المخرجات:**\n```\n{output_preview}\n```"
        else:
            status_text += "\n✅ **تم التنفيذ بدون مخرجات**"
    
    elif task.status == 'failed':
        if task.error:
            error_preview = task.error[:300] + ("..." if len(task.error) > 300 else "")
            status_text += f"\n❌ **الخطأ:**\n```\n{error_preview}\n```"
    
    update.message.reply_text(status_text, parse_mode='Markdown')

def my_tasks_command(update: Update, context: CallbackContext):
    """عرض مهام المستخدم الأخيرة"""
    user = update.effective_user
    user_tasks = bot.get_user_tasks(user.id)
    
    if not user_tasks:
        update.message.reply_text("📭 لم تقم بتنفيذ أي مهام بعد")
        return
    
    tasks_text = "📋 **مهامك الأخيرة:**\n\n"
    
    for i, task in enumerate(user_tasks, 1):
        status_icon = '✅' if task.status == 'completed' else '❌' if task.status == 'failed' else '⏳'
        time_str = task.start_time.strftime('%H:%M') if task.start_time else 'N/A'
        
        code_preview = task.code[:30] + "..." if len(task.code) > 30 else task.code
        tasks_text += f"{i}. {status_icon} `{task.id}`\n"
        tasks_text += f"   📝 {code_preview}\n"
        tasks_text += f"   🕐 {time_str} | ⏱️ {task.execution_time:.2f}s\n\n"
    
    update.message.reply_text(tasks_text, parse_mode='Markdown')

def dashboard_command(update: Update, context: CallbackContext):
    """لوحة التحكم للمشرفين"""
    user = update.effective_user
    
    if user.id not in ADMIN_USERS:
        update.message.reply_text("⛔ ليس لديك صلاحية الوصول إلى لوحة التحكم")
        return
    
    system_stats = bot.system_stats
    recent_tasks = bot.get_recent_tasks(3)
    
    dashboard_text = f"""
⚙️ **لوحة تحكم المشرف**
🚀 **يعمل على Railway**

📊 **إحصائيات النظام:**
🔢 إجمالي المهام: {system_stats['total_tasks']}
✅ ناجحة: {system_stats['successful_tasks']}
❌ فاشلة: {system_stats['failed_tasks']}

📋 **أحدث المهام:**
"""
    
    for task in recent_tasks:
        status_icon = '✅' if task.status == 'completed' else '❌' if task.status == 'failed' else '⏳'
        dashboard_text += f"{status_icon} {task.username}: {task.code[:20]}...\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 تحديث", callback_data='refresh_dashboard')],
        [InlineKeyboardButton("🗑️ تنظيف الذاكرة", callback_data='cleanup')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(dashboard_text, parse_mode='Markdown', reply_markup=reply_markup)

def button_callback(update: Update, context: CallbackContext):
    """معالجة ضغطات الأزرار"""
    query = update.callback_query
    data = query.data
    
    if data == 'new_code':
        query.answer()
        query.edit_message_text(
            "📝 **إرسال الكود للتنفيذ**\n\n"
            "يمكنك إرسال الكود الآن:\n"
            "• بشكل مباشر\n"
            "• أو محاط بعلامات ```\n\n"
            "⏱️ **ملاحظة:** وقت التنفيذ محدود بـ 30 ثانية",
            parse_mode='Markdown'
        )
    
    elif data == 'my_tasks':
        query.answer()
        my_tasks_command(update, context)
    
    elif data == 'dashboard':
        query.answer()
        dashboard_command(update, context)
    
    elif data == 'refresh_dashboard':
        query.answer("🔄 يتم تحديث لوحة التحكم")
        dashboard_command(update, context)
    
    elif data == 'cleanup':
        query.answer("🗑️ جاري تنظيف الذاكرة...")
        # تنظيف المهام القديمة
        cutoff_time = datetime.now() - timedelta(hours=1)
        old_tasks = [tid for tid, task in bot.tasks.items() 
                    if task.end_time and task.end_time < cutoff_time]
        
        for task_id in old_tasks:
            del bot.tasks[task_id]
        
        query.edit_message_text("✅ تم تنظيف المهام القديمة (أكثر من ساعة)")
    
    else:
        query.answer()

def help_command(update: Update, context: CallbackContext):
    """عرض المساعدة"""
    help_text = """
📚 **مساعدة بوت تنفيذ الكود**

🤖 **الأوامر المتاحة:**
/start - بدء البوت وعرض القائمة
/help - عرض هذه الرسالة
/status <task_id> - عرض حالة مهمة
/mytasks - عرض مهامي الأخيرة

🚀 **كيفية الاستخدام:**
1. أرسل كود Python مباشرة
2. أو استخدم علامات ``` للكود الطويل
3. انتظر تنفيذ المهمة
4. تابع حالة المهمة بـ /status

⚠️ **ملاحظات هامة:**
• وقت التنفيذ: 30 ثانية كحد أقصى
• الذاكرة محدودة
• الكود يعمل في بيئة معزولة

📞 **للمساعدة:** @your_username
"""
    update.message.reply_text(help_text)

# ============ التنفيذ الرئيسي المعدل للعمل على Railway ============

def main():
    """الدالة الرئيسية المعدلة للعمل على Railway"""
    if not BOT_TOKEN:
        print("❌ يرجى تعيين متغير البيئة BOT_TOKEN على Railway")
        print("💡 اذهب إلى Settings → Variables في لوحة تحكم Railway")
        return
    
    print(f"🚀 بدء تشغيل البوت على Railway...")
    print(f"🤖 توكن البوت: {BOT_TOKEN[:10]}...")
    print(f"👥 المشرفون: {ADMIN_USERS}")
    print(f"🌐 PORT: {PORT}")
    
    # إنشاء Updater مع إعدادات مناسبة لل Railway
    updater = Updater(
        BOT_TOKEN,
        use_context=True,
        request_kwargs={
            'read_timeout': 30,
            'connect_timeout': 30,
        }
    )
    
    dp = updater.dispatcher
    
    # إضافة المعالجات
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("status", status_command))
    dp.add_handler(CommandHandler("mytasks", my_tasks_command))
    dp.add_handler(CommandHandler("dashboard", dashboard_command))
    
    # معالج الكود
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_code_input))
    
    # معالج الأزرار
    dp.add_handler(CallbackQueryHandler(button_callback))
    
    # على Railway، نستخدم Webhook إذا كان متاحاً، وإلا Polling
    if WEBHOOK_URL:
        print(f"🌐 استخدام Webhook: {WEBHOOK_URL}")
        updater.start_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        print("🔄 استخدام Polling (لتطوير محلي)")
        updater.start_polling(
            timeout=30,
            drop_pending_updates=True
        )
    
    print("✅ البوت يعمل بنجاح!")
    print("📱 اذهب إلى التيليجرام واستخدم /start")
    
    # البقاء نشطاً
    updater.idle()

if __name__ == "__main__":
    main()
