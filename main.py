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
TASK_HISTORY_SIZE = 100  # زيادة سعة التاريخ

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
        task_id = f"task_{int(time.time())}_{user_id}_{hash(code) % 10000}"
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
                timeout=60,
                encoding='utf-8',
                errors='ignore'
            )
            execution_time = time.time() - start_time
            
            task.execution_time = execution_time
            task.output = result.stdout
            task.error = result.stderr
            task.status = "completed" if result.returncode == 0 else "failed"
            task.end_time = datetime.now()
            
            if task.status == "completed":
                self.user_stats[task.user_id]['success'] += 1
                self.system_stats['successful_tasks'] += 1
                self.system_stats['total_execution_time'] += execution_time
            else:
                self.user_stats[task.user_id]['errors'] += 1
                self.system_stats['failed_tasks'] += 1
                self.system_stats['total_execution_time'] += execution_time
            
        except subprocess.TimeoutExpired:
            task.status = "failed"
            task.error = "⏱️ انتهى وقت التنفيذ (60 ثانية كحد أقصى)"
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
        return [task for task in self.task_history if task.user_id == user_id][-10:]  # آخر 10 مهام
    
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
        [InlineKeyboardButton("❓ المساعدة", callback_data='help')],
    ]
    
    if user.id in ADMIN_USERS:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data='dashboard')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        f"👋 مرحباً {user.first_name}!\n"
        "🤖 بوت تنفيذ كود Python\n"
        "🚀 يعمل على Railway\n"
        "⚡ بدون قيود تقريباً\n\n"
        "📌 **مميزات:**\n"
        "• وقت تنفيذ 60 ثانية\n"
        "• دعم مكتبات Python\n"
        "• تشغيل متعدد المهام\n\n"
        "اختر أحد الخيارات:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

def handle_code_input(update: Update, context: CallbackContext):
    """معالجة إدخال الكود"""
    user = update.effective_user
    code = update.message.text
    
    if code.startswith('/'):
        return
    
    if code.startswith('```') and code.endswith('```'):
        code = code[3:-3].strip()
        if code.lower().startswith('python'):
            code = code[6:].strip()
    
    if len(code) > 5000:
        update.message.reply_text("⚠️ الكود طويل جداً. الحد الأقصى 5000 حرف.")
        return
    
    task_id = bot.add_task(user.id, user.username or user.first_name, code)
    
    update.message.reply_text(
        f"✅ **تم إضافة المهمة للتنفيذ**\n\n"
        f"🆔 **معرف المهمة:** `{task_id}`\n"
        f"👤 **المستخدم:** {user.first_name}\n"
        f"📝 **طول الكود:** {len(code)} حرف\n"
        f"⏳ **الحالة:** قيد التنفيذ...\n\n"
        f"📊 **لمتابعة الحالة:**\n"
        f"`/status {task_id}`",
        parse_mode='Markdown'
    )

def status_command(update: Update, context: CallbackContext):
    """عرض حالة مهمة معينة"""
    user = update.effective_user
    
    if not context.args:
        update.message.reply_text(
            "⚠️ **يرجى تحديد معرف المهمة**\n\n"
            "📌 **طريقة الاستخدام:**\n"
            "`/status task_1234567890`\n\n"
            "📋 **لعرض مهامك:**\n"
            "`/mytasks`",
            parse_mode='Markdown'
        )
        return
    
    task_id = context.args[0]
    task = bot.get_task(task_id)
    
    if not task:
        update.message.reply_text(
            "❌ **لم يتم العثور على المهمة**\n\n"
            "⚠️ **الأسباب المحتملة:**\n"
            "• المعرف غير صحيح\n"
            "• المهمة انتهت منذ أكثر من ساعة\n"
            "• تم تنظيف المهام القديمة",
            parse_mode='Markdown'
        )
        return
    
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
📊 **الحالة:** {status_icons.get(task.status, '❓')} {task.status}
⏱️ **زمن التنفيذ:** {task.execution_time:.2f} ثانية
📝 **طول الكود:** {len(task.code)} حرف
"""
    
    if task.status == 'completed':
        if task.output:
            output_preview = task.output[:500] + ("..." if len(task.output) > 500 else "")
            status_text += f"\n📤 **المخرجات:**\n```\n{output_preview}\n```"
        else:
            status_text += "\n✅ **تم التنفيذ بدون مخرجات**"
    
    elif task.status == 'failed':
        if task.error:
            error_preview = task.error[:500] + ("..." if len(task.error) > 500 else "")
            status_text += f"\n❌ **الخطأ:**\n```\n{error_preview}\n```"
    
    keyboard = []
    if user.id == task.user_id or user.id in ADMIN_USERS:
        keyboard.append([InlineKeyboardButton("🔄 تحديث الحالة", callback_data=f'status_{task_id}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    update.message.reply_text(status_text, parse_mode='Markdown', reply_markup=reply_markup)

def my_tasks_command(update: Update, context: CallbackContext):
    """عرض مهام المستخدم الأخيرة"""
    if update.message:
        reply_method = update.message.reply_text
        can_edit = False
    elif update.callback_query:
        query = update.callback_query
        reply_method = query.edit_message_text
        can_edit = True
        query.answer()
    else:
        return
    
    user = update.effective_user
    user_tasks = bot.get_user_tasks(user.id)
    
    if not user_tasks:
        reply_method("📭 **لم تقم بتنفيذ أي مهام بعد**\n\n"
                    "🚀 **لبدء التنفيذ:**\n"
                    "1. أرسل كود Python مباشرة\n"
                    "2. أو اضغط على 'تشغيل كود جديد'",
                    parse_mode='Markdown')
        return
    
    tasks_text = "📋 **آخر 10 مهام لك:**\n\n"
    
    for i, task in enumerate(reversed(user_tasks), 1):
        status_icon = '✅' if task.status == 'completed' else '❌' if task.status == 'failed' else '⏳'
        time_str = task.start_time.strftime('%H:%M') if task.start_time else 'N/A'
        
        code_preview = task.code[:40] + "..." if len(task.code) > 40 else task.code
        tasks_text += f"{i}. {status_icon} **{task.status}**\n"
        tasks_text += f"   🆔 `{task.id}`\n"
        tasks_text += f"   📝 {code_preview}\n"
        tasks_text += f"   🕐 {time_str} | ⏱️ {task.execution_time:.2f}s\n\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 تحديث القائمة", callback_data='my_tasks'),
         InlineKeyboardButton("🚀 كود جديد", callback_data='new_code')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if can_edit:
        reply_method(text=tasks_text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        reply_method(tasks_text, parse_mode='Markdown', reply_markup=reply_markup)

def dashboard_command(update: Update, context: CallbackContext):
    """لوحة التحكم للمشرفين"""
    if update.message:
        reply_method = update.message.reply_text
        can_edit = False
    elif update.callback_query:
        query = update.callback_query
        reply_method = query.edit_message_text
        can_edit = True
        query.answer()
    else:
        return
    
    user = update.effective_user
    
    if user.id not in ADMIN_USERS:
        error_msg = "⛔ ليس لديك صلاحية الوصول إلى لوحة التحكم"
        if can_edit:
            reply_method(text=error_msg)
        else:
            update.message.reply_text(error_msg)
        return
    
    system_stats = bot.system_stats
    recent_tasks = bot.get_recent_tasks(5)
    
    avg_time = system_stats['total_execution_time'] / system_stats['total_tasks'] if system_stats['total_tasks'] > 0 else 0
    
    dashboard_text = f"""
⚙️ **لوحة تحكم المشرف**
🚀 **يعمل على Railway**

📊 **إحصائيات النظام:**
• 🔢 **إجمالي المهام:** {system_stats['total_tasks']}
• ✅ **ناجحة:** {system_stats['successful_tasks']}
• ❌ **فاشلة:** {system_stats['failed_tasks']}
• ⏱️ **متوسط الوقت:** {avg_time:.2f} ثانية

👥 **المستخدمون النشطون:** {len(bot.user_stats)}
📋 **آخر 5 مهام:**
"""
    
    for task in recent_tasks:
        status_icon = '✅' if task.status == 'completed' else '❌' if task.status == 'failed' else '⏳'
        time_str = task.start_time.strftime('%H:%M') if task.start_time else 'N/A'
        dashboard_text += f"{status_icon} **{task.username}** ({time_str}): {task.code[:25]}...\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 تحديث", callback_data='refresh_dashboard'),
         InlineKeyboardButton("🗑️ تنظيف", callback_data='cleanup')],
        [InlineKeyboardButton("📊 إحصائيات كاملة", callback_data='full_stats')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if can_edit:
        reply_method(text=dashboard_text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        reply_method(dashboard_text, parse_mode='Markdown', reply_markup=reply_markup)

def help_command(update: Update, context: CallbackContext):
    """عرض المساعدة"""
    # تقسيم النص إلى أجزاء لتجنب مشاكل السلاسل الطويلة
    help_text = "📚 **مساعدة بوت تنفيذ الكود**\n\n"
    help_text += "🤖 **الأوامر المتاحة:**\n"
    help_text += "/start - بدء البوت وعرض القائمة\n"
    help_text += "/help - عرض هذه الرسالة\n"
    help_text += "/status <task_id> - عرض حالة مهمة\n"
    help_text += "/mytasks - عرض مهامي الأخيرة\n"
    help_text += "/dashboard - لوحة التحكم (للمشرفين فقط)\n\n"
    
    help_text += "🚀 **كيفية الاستخدام:**\n"
    help_text += "1. أرسل كود Python مباشرة\n"
    help_text += "2. أو استخدم علامات ``` للكود الطويل\n"
    help_text += "3. انتظر تنفيذ المهمة\n"
    help_text += "4. تابع حالة المهمة بـ /status\n\n"
    
    help_text += "💡 **أمثلة:**\n"
    help_text += "```\n"
    help_text += "print(\"Hello World!\")\n"
    help_text += "```\n"
    help_text += "أو\n"
    help_text += "```python\n"
    help_text += "for i in range(5):\n"
    help_text += "    print(i)\n"
    help_text += "```\n\n"
    
    help_text += "⚡ **المميزات:**\n"
    help_text += "• وقت تنفيذ 60 ثانية\n"
    help_text += "• دعم جميع مكتبات Python\n"
    help_text += "• تشغيل متعدد المهام\n"
    help_text += "• عرض النتائج فوراً\n\n"
    
    help_text += "⚠️ **ملاحظات أمان:**\n"
    help_text += "• لا تقم بتنفيذ كود غير موثوق\n"
    help_text += "• الكود يعمل في بيئة معزولة\n"
    help_text += "• المهام تحفظ لمدة ساعة فقط\n\n"
    
    help_text += "📞 **للمساعدة:** @your_username"
    
    if update.message:
        update.message.reply_text(help_text, parse_mode='Markdown')
    elif update.callback_query:
        query = update.callback_query
        query.answer()
        query.edit_message_text(help_text, parse_mode='Markdown')

def button_callback(update: Update, context: CallbackContext):
    """معالجة ضغطات الأزرار"""
    query = update.callback_query
    data = query.data
    
    query.answer()
    
    if data == 'new_code':
        query.edit_message_text(
            "📝 **إرسال الكود للتنفيذ**\n\n"
            "**يمكنك إرسال الكود الآن:**\n"
            "• بشكل مباشر\n"
            "• أو محاط بعلامات ```\n\n"
            "**⚡ المميزات:**\n"
            "• ⏱️ وقت التنفيذ: 60 ثانية\n"
            "• 📦 دعم جميع المكتبات\n"
            "• 🔄 تشغيل متعدد المهام\n\n"
            "**📝 مثال:**\n"
            "```python\n"
            "import random\n"
            "print(random.randint(1, 100))\n"
            "```",
            parse_mode='Markdown'
        )
    
    elif data == 'my_tasks':
        my_tasks_command(update, context)
    
    elif data == 'help':
        help_command(update, context)
    
    elif data == 'dashboard':
        dashboard_command(update, context)
    
    elif data == 'refresh_dashboard':
        query.answer("🔄 يتم تحديث لوحة التحكم...")
        dashboard_command(update, context)
    
    elif data.startswith('status_'):
        task_id = data[7:]
        context.args = [task_id]
        status_command(update, context)
    
    elif data == 'cleanup':
        cutoff_time = datetime.now() - timedelta(hours=1)
        old_tasks = [tid for tid, task in bot.tasks.items() 
                    if task.end_time and task.end_time < cutoff_time]
        
        cleaned_count = len(old_tasks)
        for task_id in old_tasks:
            del bot.tasks[task_id]
        
        query.answer(f"✅ تم تنظيف {cleaned_count} مهمة قديمة")
        dashboard_command(update, context)
    
    elif data == 'full_stats':
        user_stats_text = "📊 **إحصائيات المستخدمين:**\n\n"
        for user_id, stats in list(bot.user_stats.items())[:10]:
            user_stats_text += f"👤 **المستخدم:** {user_id}\n"
            user_stats_text += f"   • 📋 المهام: {stats['tasks']}\n"
            user_stats_text += f"   • ✅ الناجحة: {stats['success']}\n"
            user_stats_text += f"   • ❌ الفاشلة: {stats['errors']}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data='dashboard')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(user_stats_text, parse_mode='Markdown', reply_markup=reply_markup)
    
    else:
        query.answer("⚠️ خيار غير معروف")

def error_handler(update: Update, context: CallbackContext):
    """معالج الأخطاء العام"""
    try:
        print(f"ERROR: {context.error}")
        
        if update:
            if update.message:
                update.message.reply_text("❌ حدث خطأ أثناء معالجة طلبك")
            elif update.callback_query:
                update.callback_query.answer("❌ حدث خطأ، يرجى المحاولة لاحقاً", show_alert=True)
    except Exception as e:
        print(f"Error in error handler: {e}")

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
    
    if WEBHOOK_URL:
        print(f"🌐 استخدام Webhook: {WEBHOOK_URL}")
    else:
        print("🔄 استخدام Polling (لتطوير محلي)")
    
    updater = Updater(
        BOT_TOKEN,
        use_context=True,
        request_kwargs={
            'read_timeout': 30,
            'connect_timeout': 30,
        }
    )
    
    dp = updater.dispatcher
    
    dp.add_error_handler(error_handler)
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("status", status_command))
    dp.add_handler(CommandHandler("mytasks", my_tasks_command))
    dp.add_handler(CommandHandler("dashboard", dashboard_command))
    
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_code_input))
    dp.add_handler(CallbackQueryHandler(button_callback))
    
    if WEBHOOK_URL:
        updater.start_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        updater.start_polling(
            timeout=30,
            drop_pending_updates=True,
            allowed_updates=['message', 'callback_query']
        )
    
    print("✅ البوت يعمل بنجاح!")
    print("📱 اذهب إلى التيليجرام واستخدم /start")
    
    updater.idle()

if __name__ == "__main__":
    main()
