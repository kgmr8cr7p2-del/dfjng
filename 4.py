import asyncio
import os
import json
import threading
import logging
import time
import random
from datetime import datetime, timedelta
import customtkinter as ctk
from aiogram import Bot, Dispatcher, types, F
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from playwright.async_api import async_playwright

# Настройка логирования для вывода в окно GUI
class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        def append():
            self.text_widget.configure(state="normal")
            self.text_widget.insert("end", msg + "\n")
            self.text_widget.see("end")
            self.text_widget.configure(state="disabled")
        self.text_widget.after(0, append)

def get_progress_bar(percent):
    length = 10
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {percent}%"

# --- КЛАСС ГЕНЕРАЦИИ (ДВИЖОК) ---
class SoraWorker:
    def __init__(self, page, status_msg, bot, chat_id):
        self.page = page
        self.status_msg = status_msg
        self.bot = bot
        self.chat_id = chat_id

    async def update_status(self, text, percent):
        """Редактирует сообщение в Telegram с прогресс-баром."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        bar = get_progress_bar(percent)
        new_text = f"⏳ **Прогресс:**\n{bar}\n📍 {text}\n🕒 {timestamp}"
        logging.info(f"Статус: {percent}% - {text} ({timestamp})")
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.status_msg.message_id,
                text=new_text,
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.5) 
        except Exception: pass

    async def get_smart_prompt(self, topic):
        try:
            await self.update_status("Вход в ChatGPT...", 10)
            await self.page.goto("https://chatgpt.com/", timeout=60000)
            await asyncio.sleep(3)
            
            await self.update_status("Поиск проекта...", 20)
            project_btn = self.page.locator('a[href*="generatsiia-video/project"]')
            await project_btn.wait_for(state="visible", timeout=15000)
            await project_btn.click()
            await asyncio.sleep(4)

            await self.update_status("Генерация промта...", 35)
            input_div = self.page.locator("#prompt-textarea")
            await input_div.wait_for(state="visible")
            
            instruction = (
                f"Create a unique, fast-paced 10-second video prompt for Sora AI: {topic}. "
                "Output ONLY the English text. Max 250 characters. Intense movement, 4k. "
                "IMPORTANT: The video must be a perfect SEAMLESS LOOP. "
                "The first and last frames must be identical so the start and end are indistinguishable."
            )
            await input_div.fill(instruction)
            await input_div.press("Enter")
            await asyncio.sleep(30)
            await self.update_status("Жду ответа GPT...", 50)
            copy_btn = self.page.locator('button:has(svg use[href*="f6d0e2"]), button[aria-label="Copy"]').last
            await copy_btn.wait_for(state="visible", timeout=70000)
            
            last_msg = self.page.locator("[data-message-author-role='assistant']").last
            return (await last_msg.inner_text()).strip().replace('"', '')
        except Exception as e:
            logging.error(f"ChatGPT Error: {e}")
            return None

    async def run_sora(self, prompt):
        try:
            await self.update_status("Вход в Sora...", 60)
            await self.page.goto("https://sora.chatgpt.com/explore", timeout=60000)
            await asyncio.sleep(5)
            
            await self.update_status("Ввод промта в Sora...", 70)
            # Улучшенный поиск поля ввода
            textarea = self.page.locator('textarea[placeholder*="create"], textarea').first
            await textarea.wait_for(state="visible", timeout=30000)
            await textarea.fill(prompt)
            
            create_btn = self.page.locator("button:has-text('Create video'), button:has-text('Generate')").first
            await create_btn.click()
            
            await asyncio.sleep(3)
            await self.page.goto("https://sora.chatgpt.com/drafts")
            
            await self.update_status("Рендеринг (270 сек)...", 85)
            await asyncio.sleep(270)
            await self.page.reload()
            await asyncio.sleep(5)

            await self.update_status("Подготовка к скачиванию...", 95)
            retries = 3
            for attempt in range(1, retries + 1):
                try:
                    await self.page.locator('a[href^="/d/"]').first.click()
                    await asyncio.sleep(5)

                    menu_btn = self.page.locator("button").filter(
                        has=self.page.locator('path[d*="M3 12a2 2 0 1 1 4 0"]')
                    ).last
                    await menu_btn.click()

                    async with self.page.expect_download(timeout=120000) as download_info:
                        # Клик по пункту меню скачивания
                        await self.page.locator('div[role="menuitem"]').filter(
                            has=self.page.locator('path[d*="M12 7.1a.9.9 0 0 1 .9.9"]')
                        ).first.click()

                    download = await download_info.value
                    path = f"video_{int(time.time())}.mp4"
                    await download.save_as(path)
                    return path
                except Exception as e:
                    logging.error(f"Sora download attempt {attempt}/{retries} failed: {e}")
                    await asyncio.sleep(5)
                    await self.page.reload()
                    await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Sora Error: {e}")
            return None

    async def upload_to_youtube(self, video_file, topic, prompt, youtube_config, publish_at=None):
        if not youtube_config.get("enabled"):
            return None

        await self.update_status("Загрузка на YouTube...", 98)
        logging.info("YouTube: начинаю загрузку файла.")

        def do_upload():
            required = ("client_id", "client_secret", "refresh_token")
            if not all(youtube_config.get(key) for key in required):
                raise ValueError("YouTube config missing client_id/client_secret/refresh_token.")

            credentials = Credentials(
                token=None,
                refresh_token=youtube_config["refresh_token"],
                token_uri=youtube_config.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=youtube_config["client_id"],
                client_secret=youtube_config["client_secret"],
                scopes=["https://www.googleapis.com/auth/youtube.upload"],
            )
            youtube = build("youtube", "v3", credentials=credentials)

            title_template = youtube_config.get("title_template", "Sora2 | {topic}")
            description_template = youtube_config.get("description_template", "{static}\n\n{prompt_text}")
            title = title_template.format(topic=topic, prompt=prompt)

            description = self._build_description(
                topic,
                prompt,
                youtube_config.get("prompt_mode", {}),
                description_template,
            )
            if youtube_config.get("append_shorts_tag", True):
                description = f"{description}\n\n#shorts"
                if "#shorts" not in title.lower():
                    title = f"{title} #shorts"

            snippet = {
                "title": title[:95],
                "description": description,
                "categoryId": str(youtube_config.get("category_id", "22")),
            }
            tags = youtube_config.get("tags") or []
            if youtube_config.get("append_shorts_tag", True) and "shorts" not in tags:
                tags = [*tags, "shorts"]
            if tags:
                snippet["tags"] = tags

            status = {
                "privacyStatus": youtube_config.get("privacy_status", "public"),
                "selfDeclaredMadeForKids": bool(youtube_config.get("made_for_kids", False)),
            }
            if publish_at:
                status["privacyStatus"] = "private"
                status["publishAt"] = publish_at

            media = MediaFileUpload(video_file, mimetype="video/mp4", resumable=True)
            request = youtube.videos().insert(
                part="snippet,status",
                body={"snippet": snippet, "status": status},
                media_body=media,
            )

            response = None
            while response is None:
                _, response = request.next_chunk()
            return response.get("id")

        video_id = await asyncio.to_thread(do_upload)
        logging.info("YouTube: загрузка завершена.")
        return video_id

    async def upload_to_tiktok(self, video_file, topic, prompt, tiktok_config, prompt_mode):
        if not tiktok_config.get("enabled"):
            return False

        await self.update_status("Загрузка на TikTok...", 98)
        logging.info("TikTok: начинаю загрузку файла.")

        page = await self.page.context.new_page()
        try:
            await page.goto("https://www.tiktok.com/upload?lang=en", timeout=60000)
            file_input = page.locator('input[type="file"]').first
            await file_input.set_input_files(video_file)

            caption_template = tiktok_config.get("caption_template", "{static}\n\n{prompt_text}")
            caption = self._build_description(topic, prompt, prompt_mode, caption_template)
            if tiktok_config.get("append_hashtags", True):
                caption = f"{caption}\n#shorts #fyp"

            caption_box = page.locator('textarea[placeholder]').first
            await caption_box.wait_for(state="visible", timeout=90000)
            await caption_box.fill(caption)

            post_button = page.locator('button:has-text("Post"), button:has-text("Publish")').first
            await post_button.wait_for(state="visible", timeout=90000)
            await post_button.click()

            success_text = page.locator('text=Your video is being uploaded')
            await success_text.wait_for(timeout=90000)
            logging.info("TikTok: загрузка завершена.")
            return True
        except Exception as e:
            logging.error(f"TikTok upload error: {e}")
            return False
        finally:
            await page.close()

    def _summarize_prompt(self, prompt, limit):
        cleaned = " ".join(prompt.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)].rstrip() + "…"

    def _build_description(self, topic, prompt, prompt_mode, template):
        full_ratio = float(prompt_mode.get("full_prompt_ratio", 0.2))
        summary_limit = int(prompt_mode.get("summary_max_chars", 140))
        static_description = prompt_mode.get("static_description", "")
        use_full_prompt = random.random() < full_ratio
        prompt_text = prompt if use_full_prompt else self._summarize_prompt(prompt, summary_limit)
        return template.format(
            topic=topic,
            prompt=prompt,
            prompt_text=prompt_text,
            static=static_description,
        ).strip()

    async def wait_for_youtube_publish(self, video_id, youtube_config, publish_at_dt=None):
        if not youtube_config.get("enabled") or not video_id:
            return

        if publish_at_dt:
            now = datetime.now().astimezone()
            wait_seconds = (publish_at_dt - now).total_seconds()
            if wait_seconds > 0:
                logging.info(
                    "Ожидание времени публикации YouTube: %s секунд.",
                    int(wait_seconds),
                )
                await asyncio.sleep(wait_seconds)

        def fetch_status():
            credentials = Credentials(
                token=None,
                refresh_token=youtube_config["refresh_token"],
                token_uri=youtube_config.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=youtube_config["client_id"],
                client_secret=youtube_config["client_secret"],
                scopes=["https://www.googleapis.com/auth/youtube.upload"],
            )
            youtube = build("youtube", "v3", credentials=credentials)
            response = youtube.videos().list(part="status", id=video_id).execute()
            items = response.get("items", [])
            if not items:
                return {}
            return items[0].get("status", {})

        poll_interval = 30
        max_checks = 120
        for _ in range(max_checks):
            status = await asyncio.to_thread(fetch_status)
            privacy = status.get("privacyStatus")
            upload_status = status.get("uploadStatus")
            if privacy == "public" and upload_status in {"processed", "uploaded"}:
                logging.info("YouTube: ролик опубликован.")
                return
            logging.info(
                "YouTube: ожидание публикации (privacy=%s, upload=%s).",
                privacy,
                upload_status,
            )
            await asyncio.sleep(poll_interval)

        logging.warning("YouTube: публикация не подтверждена в отведенное время.")

# --- ИНТЕРФЕЙС ГРАФИЧЕСКОГО ОКНА ---
class SoraApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Sora Factory Pro")
        self.geometry("1000x700")
        
        self.active_sessions = {}
        self.bot_running = False
        self.loop = None
        self.config = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Боковая панель
        self.sidebar = ctk.CTkFrame(self, width=320)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(self.sidebar, text="НАСТРОЙКИ", font=("Arial", 20, "bold")).pack(pady=15)
        ctk.CTkLabel(self.sidebar, text="Telegram", font=("Arial", 14, "bold")).pack(pady=(5, 0))
        self.token_entry = self.create_input("Telegram Bot Token")
        self.chat_entry = self.create_input("Target Chat ID (Optional)")
        
        ctk.CTkLabel(self.sidebar, text="Темы кнопок:", font=("Arial", 12, "bold")).pack(pady=(10, 0))
        self.topics_text = ctk.CTkTextbox(self.sidebar, height=250)
        self.topics_text.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(self.sidebar, text="YouTube расписание:", font=("Arial", 12, "bold")).pack(pady=(10, 0))
        self.publish_time_entry = self.create_input("Время 1-го видео (HH:MM)")
        self.publish_interval_entry = self.create_input("Периодичность (мин)")
        self.publish_count_entry = self.create_input("Сколько видео загрузить")

        self.save_btn = ctk.CTkButton(self.sidebar, text="💾 СОХРАНИТЬ КОНФИГ", command=self.save_config, fg_color="#4A4A4A")
        self.save_btn.pack(pady=5)

        self.start_btn = ctk.CTkButton(self.sidebar, text="СТАРТ БОТА", fg_color="green", command=self.start_bot)
        self.start_btn.pack(pady=5)
        
        self.stop_btn = ctk.CTkButton(self.sidebar, text="СТОП БОТА", fg_color="red", command=self.stop_bot, state="disabled")
        self.stop_btn.pack(pady=5)

        # Окно логов
        self.log_panel = ctk.CTkFrame(self)
        self.log_panel.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.log_panel.grid_rowconfigure(1, weight=1)
        self.log_panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.log_panel, text="ЛОГИ", font=("Arial", 16, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(5, 0)
        )
        self.log_view = ctk.CTkTextbox(self.log_panel, state="disabled", font=("Consolas", 12))
        self.log_view.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        self.load_config()
        logging.getLogger().addHandler(TextHandler(self.log_view))
        logging.getLogger().setLevel(logging.INFO)

    def create_input(self, placeholder):
        entry = ctk.CTkEntry(self.sidebar, placeholder_text=placeholder)
        entry.pack(fill="x", padx=10, pady=5)
        return entry

    def load_config(self):
        defaults = {
            "bot_token": "",
            "target_chat_id": "",
            "topics": ["Фракталы", "Киберпанк", "Жидкости"],
            "youtube": {
                "enabled": False,
                "client_id": "",
                "client_secret": "",
                "refresh_token": "",
                "token_uri": "https://oauth2.googleapis.com/token",
                "title_template": "Sora2 | {topic}",
                "description_template": "{static}\n\n{prompt_text}",
                "append_shorts_tag": True,
                "prompt_mode": {
                    "full_prompt_ratio": 0.2,
                    "summary_max_chars": 140,
                    "static_description": "AI video from Sora2.",
                },
                "tags": ["sora2", "sora", "ai video"],
                "category_id": "22",
                "privacy_status": "public",
                "made_for_kids": False,
                "schedule": {
                    "start_time": "",
                    "interval_minutes": 0,
                    "count": 0,
                },
            },
            "tiktok": {
                "enabled": False,
                "caption_template": "{static}\n\n{prompt_text}",
                "append_hashtags": True,
                "prompt_mode": {
                    "full_prompt_ratio": 0.2,
                    "summary_max_chars": 140,
                    "static_description": "AI video from Sora2.",
                },
            },
        }
        if os.path.exists("config.json"):
            with open("config.json", "r", encoding="utf-8") as f:
                self.config = json.load(f)
                merged = {**defaults, **self.config}
                merged["youtube"] = {**defaults["youtube"], **self.config.get("youtube", {})}
                merged["tiktok"] = {**defaults["tiktok"], **self.config.get("tiktok", {})}
                merged["tiktok"]["prompt_mode"] = {
                    **defaults["tiktok"]["prompt_mode"],
                    **self.config.get("tiktok", {}).get("prompt_mode", {}),
                }
                self.config = merged
                self.token_entry.insert(0, self.config.get("bot_token", ""))
                self.chat_entry.insert(0, self.config.get("target_chat_id", ""))
                self.topics_text.insert("1.0", "\n".join(self.config.get("topics", [])))
                schedule = self.config.get("youtube", {}).get("schedule", {})
                self.publish_time_entry.insert(0, schedule.get("start_time", ""))
                self.publish_interval_entry.insert(0, str(schedule.get("interval_minutes", "")))
                self.publish_count_entry.insert(0, str(schedule.get("count", "")))
        else:
            self.config = defaults
            self.topics_text.insert("1.0", "\n".join(self.config["topics"]))
            self.save_config()

    def save_config(self):
        topics = [t.strip() for t in self.topics_text.get("1.0", "end").split("\n") if t.strip()]
        schedule = self.config.get("youtube", {}).get("schedule", {})
        schedule["start_time"] = self.publish_time_entry.get().strip()
        try:
            schedule["interval_minutes"] = int(self.publish_interval_entry.get() or 0)
        except ValueError:
            schedule["interval_minutes"] = 0
        try:
            schedule["count"] = int(self.publish_count_entry.get() or 0)
        except ValueError:
            schedule["count"] = 0
        self.config = {
            "bot_token": self.token_entry.get(),
            "target_chat_id": self.chat_entry.get(),
            "topics": topics,
            "youtube": {
                **self.config.get("youtube", {}),
                "schedule": schedule,
            },
            "tiktok": self.config.get("tiktok", {}),
        }
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
        logging.info("Конфигурация сохранена.")

    def update_target_chat_id(self, new_chat_id):
        self.config["target_chat_id"] = str(new_chat_id)
        self.chat_entry.delete(0, "end")
        self.chat_entry.insert(0, str(new_chat_id))
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
        logging.info("Обновлен target_chat_id: %s", new_chat_id)

    async def safe_send_message(self, bot, chat_id, text, **kwargs):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramMigrateToChat as e:
            self.update_target_chat_id(e.migrate_to_chat_id)
            return await bot.send_message(e.migrate_to_chat_id, text, **kwargs)

    async def safe_send_video(self, bot, chat_id, video, **kwargs):
        try:
            return await bot.send_video(chat_id, video, **kwargs)
        except TelegramMigrateToChat as e:
            self.update_target_chat_id(e.migrate_to_chat_id)
            return await bot.send_video(e.migrate_to_chat_id, video, **kwargs)

    def start_bot(self):
        self.save_config()
        if not self.config["bot_token"]:
            logging.error("Токен бота пуст!")
            return
        self.bot_running = True
        self.start_btn.configure(state="disabled", text="РАБОТАЕТ")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self.run_async_loop, daemon=True).start()

    def stop_bot(self):
        self.bot_running = False
        self.active_sessions.clear()
        if self.loop: self.loop.call_soon_threadsafe(self.loop.stop)
        self.start_btn.configure(state="normal", text="СТАРТ БОТА")
        self.stop_btn.configure(state="disabled")

    def run_async_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.bot_main())
        except Exception: pass

    async def bot_main(self):
        bot = Bot(token=self.config["bot_token"])
        dp = Dispatcher()

        @dp.message(F.text == "/start")
        async def cmd_start(m: types.Message):
            builder = ReplyKeyboardBuilder()
            for t in self.config["topics"]: builder.button(text=t)
            builder.button(text="⏹ ОСТАНОВИТЬ")
            await m.answer("Выберите тему:", reply_markup=builder.adjust(2).as_markup(resize_keyboard=True))

        @dp.message(F.text == "⏹ ОСТАНОВИТЬ")
        async def cmd_stop(m: types.Message):
            self.active_sessions[m.from_user.id] = False
            await m.answer("🛑 Останавливаю конвейер...")

        @dp.message()
        async def handle_loop(message: types.Message):
            if message.text not in self.config["topics"] or not self.bot_running: return
            
            user_id = message.from_user.id
            topic = message.text
            self.active_sessions[user_id] = True
            dest = self.config["target_chat_id"] if self.config["target_chat_id"] else message.chat.id
            
            await message.answer(f"🚀 Запуск конвейера: {topic}")

            async with async_playwright() as p:
                try:
                    browser = None
                    for attempt in range(1, 4):
                        try:
                            browser = await p.chromium.connect_over_cdp("http://localhost:9222", timeout=60000)
                            break
                        except Exception as e:
                            logging.error(f"Браузер Error (connect attempt {attempt}/3): {e}")
                            await asyncio.sleep(5)
                    if not browser:
                        await self.safe_send_message(bot, dest, "❌ Не удалось подключиться к браузеру.")
                        return
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else await context.new_page()
                    
                    schedule_config = self.config.get("youtube", {}).get("schedule", {})
                    publish_at = None
                    remaining_uploads = schedule_config.get("count") or 0
                    start_time = schedule_config.get("start_time", "")
                    interval_minutes = schedule_config.get("interval_minutes") or 0
                    if start_time:
                        now = datetime.now().astimezone()
                        try:
                            start_hour, start_minute = map(int, start_time.split(":"))
                            publish_at = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                            if publish_at <= now:
                                publish_at += timedelta(days=1)
                            logging.info(
                                "YouTube расписание: стартовая публикация %s.",
                                publish_at.isoformat(timespec="seconds"),
                            )
                        except ValueError:
                            logging.error("Неверный формат времени публикации (ожидается HH:MM).")
                            publish_at = None
                    schedule_active = bool(publish_at and remaining_uploads > 0)
                    if schedule_active and interval_minutes <= 0:
                        logging.error("Периодичность публикации должна быть больше 0 минут.")
                        interval_minutes = 1

                    while self.active_sessions.get(user_id) and self.bot_running:
                        # Создаем сообщение прогресса
                        status_msg = await self.safe_send_message(bot, dest, "🎬 Начало цикла...")
                        dest = status_msg.chat.id
                        worker = SoraWorker(page, status_msg, bot, dest)
                        
                        # 1. ChatGPT
                        prompt = await worker.get_smart_prompt(topic)
                        if not prompt:
                            await self.safe_send_message(dest=dest, bot=bot, text="❌ Ошибка ChatGPT. Повтор...")
                            await asyncio.sleep(20)
                            continue
                            
                        # 2. Sora
                        video_file = await worker.run_sora(prompt)
                        
                        if video_file and os.path.exists(video_file):
                            youtube_id = None
                            try:
                                scheduled_publish_at = None
                                scheduled_publish_at_dt = None
                                if schedule_active and remaining_uploads > 0:
                                    scheduled_publish_at_dt = publish_at
                                    scheduled_publish_at = publish_at.isoformat(timespec="seconds")
                                    publish_at += timedelta(minutes=interval_minutes)
                                    remaining_uploads -= 1
                                    logging.info(
                                        "YouTube расписание: поставлено на публикацию %s.",
                                        scheduled_publish_at,
                                    )
                                youtube_id = await worker.upload_to_youtube(
                                    video_file,
                                    topic,
                                    prompt,
                                    self.config.get("youtube", {}),
                                    publish_at=scheduled_publish_at,
                                )
                            except Exception as e:
                                logging.error(f"YouTube upload error: {e}")

                            await worker.update_status("Готово! Отправка...", 100)
                            await self.safe_send_video(
                                bot,
                                dest,
                                types.FSInputFile(video_file),
                                caption=f"✅ Тема: {topic}\n📝 Промт: {prompt}",
                            )
                            tiktok_ok = await worker.upload_to_tiktok(
                                video_file,
                                topic,
                                prompt,
                                self.config.get("tiktok", {}),
                                self.config.get("tiktok", {}).get("prompt_mode", {}),
                            )
                            if tiktok_ok:
                                await self.safe_send_message(bot, dest, "🎵 Видео опубликовано в TikTok.")
                            if youtube_id:
                                await self.safe_send_message(bot, dest, f"📺 Загружено на YouTube: https://youtu.be/{youtube_id}")

                            await worker.wait_for_youtube_publish(
                                youtube_id,
                                self.config.get("youtube", {}),
                                scheduled_publish_at_dt,
                            )

                            os.remove(video_file)
                            try: await bot.delete_message(status_msg.chat.id, status_msg.message_id)
                            except: pass
                        else:
                            await self.safe_send_message(bot, dest, "❌ Sora не отдала файл.")

                        if schedule_active and remaining_uploads == 0:
                            self.active_sessions[user_id] = False
                            break

                        if not self.active_sessions.get(user_id): break
                        await asyncio.sleep(10)
                except Exception as e:
                    logging.error(f"Браузер Error: {e}")

        logging.info("Бот запущен и ожидает команд...")
        await dp.start_polling(bot)

if __name__ == "__main__":
    app = SoraApp()
    app.mainloop()
