import os
import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message, User, BufferedInputFile
from io import BytesIO
from google import genai
from google.genai import types
from PIL import Image
import re
import psycopg2
from concurrent.futures import ThreadPoolExecutor
 
# Настройка логирования
logging.basicConfig(level=logging.INFO)
 
# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN4")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY4")
DATABASE_URL = os.getenv("DATABASE_URL4")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
 
# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
 
# Инициализация нового Gemini клиента
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY не установлен.")
    exit(1)
 
client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-1.5-flash"
 
PREDEFINED_PROMPT = """Проанализируй ладони по предоставленным фотографиям, используя опыт профессиональной хиромантии.
 
Обрати внимание на форму кисти, длину и пропорции пальцев, текстуру кожи, рисунок линий (жизни, сердца, ума, судьбы) и их взаимодействие.
 
Составь глубокий и структурированный портрет моей личности: опиши сильные и слабые стороны характера, особенности мышления, эмоциональную сферу, карьерный потенциал, природные таланты и вероятные жизненные вызовы.
Отдельно сделай акцент на:
- ключевые моменты, когда возможны важные повороты судьбы,
- скрытые ресурсы, которые стоит развивать,
- рекомендации для гармоничного раскрытия моих способностей.
 
Пиши профессионально, с опорой на системный подход и лучшие практики анализа ладони. Избегай общих фраз - стремись к конкретике и точности"""
 
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
db_executor = ThreadPoolExecutor()
 
# --- База данных ---
 
def get_db_connection(db_url):
    if not db_url:
        raise ValueError("DATABASE_URL не установлен.")
    return psycopg2.connect(db_url)
 
def init_db(db_url):
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                telegram_username VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS palm_photos (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(id),
                telegram_file_id VARCHAR(255) NOT NULL,
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                telegram_username VARCHAR(255),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS palm_readings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(id),
                photo_id INT REFERENCES palm_photos(id),
                prompt_text TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                telegram_username VARCHAR(255),
                read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logging.info("Таблицы базы данных проверены/созданы успешно.")
    except Exception as e:
        logging.error(f"Ошибка при инициализации базы данных: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
 
def get_or_create_user_db(db_url: str, user: User) -> int:
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE id = %s", (user.id,))
        existing_user = cur.fetchone()
        if not existing_user:
            cur.execute(
                "INSERT INTO users (id, telegram_username, first_name, last_name) VALUES (%s, %s, %s, %s) RETURNING id;",
                (user.id, user.username, user.first_name, user.last_name)
            )
            result = cur.fetchone()
            if result is None:
                raise ValueError("Не удалось создать пользователя")
            user_id = result[0]
            conn.commit()
            logging.info(f"Новый пользователь {user_id} ({user.username}) добавлен.")
        else:
            user_id = existing_user[0]
            logging.info(f"Пользователь {user_id} ({user.username}) уже существует.")
        return user_id
    except Exception as e:
        logging.error(f"Ошибка при получении/создании пользователя {user.id}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
 
def save_photo_info_db(db_url: str, user_id: int, telegram_file_id: str, first_name: str, last_name: str, telegram_username: str) -> int:
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO palm_photos (user_id, telegram_file_id, first_name, last_name, telegram_username) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
            (user_id, telegram_file_id, first_name, last_name, telegram_username)
        )
        result = cur.fetchone()
        if result is None:
            raise ValueError("Не удалось сохранить фото")
        photo_id = result[0]
        conn.commit()
        logging.info(f"Фото сохранено с photo_id {photo_id} для пользователя {user_id}.")
        return photo_id
    except Exception as e:
        logging.error(f"Ошибка при сохранении фото для пользователя {user_id}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
 
def save_ai_reading_db(db_url: str, user_id: int, photo_id: int, prompt_text: str, ai_response: str, first_name: str, last_name: str, telegram_username: str):
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO palm_readings (user_id, photo_id, prompt_text, ai_response, first_name, last_name, telegram_username) VALUES (%s, %s, %s, %s, %s, %s, %s);",
            (user_id, photo_id, prompt_text, ai_response, first_name, last_name, telegram_username)
        )
        conn.commit()
        logging.info(f"Ответ ИИ для пользователя {user_id} сохранен.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении ответа ИИ для пользователя {user_id}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
 
# --- Вспомогательные функции ---
 
async def download_file_by_id(file_id: str) -> BytesIO:
    try:
        file_info = await bot.get_file(file_id)
        file_bytes_io = await bot.download_file(file_info.file_path)
        logging.info(f"Файл {file_id} успешно загружен.")
        return file_bytes_io
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {file_id}: {e}")
        raise
 
def split_text_into_chunks(text: str, max_length: int) -> list:
    if len(text) <= max_length:
        return [text]
    chunks = []
    current_chunk = ""
    paragraphs = text.split('\n\n')
    for paragraph in paragraphs:
        if len(paragraph) > max_length:
            sentences = paragraph.split('. ')
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if not sentence.endswith('.'):
                    sentence += '.'
                if len(current_chunk) + len(sentence) + 1 <= max_length:
                    current_chunk += (' ' + sentence if current_chunk else sentence)
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
        else:
            if len(current_chunk) + len(paragraph) + 2 <= max_length:
                current_chunk += ('\n\n' + paragraph if current_chunk else paragraph)
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks
 
def is_russian(text: str) -> bool:
    return bool(re.search(r'[а-яА-Я]', text))
 
async def call_gemini(prompt: str, image: Image.Image, retries: int = 3, delay: int = 40) -> str:
    """Вызов нового Gemini API с retry при ошибке 429."""
    img_bytes = BytesIO()
    image.save(img_bytes, format='JPEG')
    img_bytes.seek(0)
    image_data = img_bytes.read()
 
    for attempt in range(retries):
        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[
                        types.Part.from_bytes(data=image_data, mime_type="image/jpeg"),
                        types.Part.from_text(text=prompt),
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.7,
                        top_p=0.95,
                        max_output_tokens=2048,
                    )
                )
            )
            return response.text.strip() if response.text else ""
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                logging.warning(f"Rate limit (попытка {attempt + 1}), жду {delay}с...")
                await asyncio.sleep(delay)
            else:
                raise
 
async def translate_to_russian(text: str) -> str:
    """Перевод на русский через новый Gemini API."""
    translate_prompt = f"Переведи на русский язык, сохраняя смысл и стиль:\n\n{text}"
    try:
        response = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[types.Part.from_text(text=translate_prompt)],
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=2048,
                )
            )
        )
        return response.text.strip() if response.text else text
    except Exception as e:
        logging.error(f"Ошибка при переводе: {e}")
        return text
 
# --- Обработчики бота ---
 
@dp.message(lambda message: message.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    telegram_user = message.from_user
    processing_message = None
    ai_response_text = ""
 
    try:
        logging.info(f"Получено фото от пользователя {user_id}")
 
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL не установлен")
 
        await asyncio.get_running_loop().run_in_executor(
            db_executor, get_or_create_user_db, DATABASE_URL, telegram_user
        )
 
        first_name = telegram_user.first_name or ""
        last_name = telegram_user.last_name or ""
        telegram_username = telegram_user.username or ""
 
        processing_message = await message.reply("Спасибо за предоставленное изображение! Идет обработка...")
        await asyncio.sleep(3)
 
        photo_file_id = message.photo[-1].file_id
 
        photo_db_id = await asyncio.get_running_loop().run_in_executor(
            db_executor, save_photo_info_db, DATABASE_URL,
            user_id, photo_file_id, first_name, last_name, telegram_username
        )
 
        # Отправка фото администратору
        if ADMIN_CHAT_ID:
            try:
                file_for_admin_bytes_io = await download_file_by_id(photo_file_id)
                buffered_file_for_admin = BufferedInputFile(
                    file=file_for_admin_bytes_io.getvalue(),
                    filename=f"palm_photo_{user_id}_{photo_db_id}.jpg"
                )
                await bot.send_photo(
                    chat_id=ADMIN_CHAT_ID,
                    photo=buffered_file_for_admin,
                    caption=(
                        f"Новое фото ладони от пользователя:\n"
                        f"ID: {user_id}\n"
                        f"Имя: {first_name} {last_name}\n"
                        f"Ник: @{telegram_username or 'нет'}"
                    )
                )
                logging.info(f"Фото отправлено администратору {ADMIN_CHAT_ID}.")
            except Exception as admin_err:
                logging.error(f"Не удалось отправить фото администратору: {admin_err}")
 
        # Загружаем фото и отправляем в Gemini
        file = await bot.get_file(photo_file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        img = Image.open(BytesIO(file_bytes_io.read()))
 
        logging.info(f"Отправка запроса в Gemini для пользователя {user_id}.")
        ai_response_text = await call_gemini(PREDEFINED_PROMPT, img)
 
        if not ai_response_text:
            ai_response_text = "К сожалению, анализ не удался. Пожалуйста, попробуйте позже с другим фото."
 
        # Замена английских ошибок на русские
        lower_response = ai_response_text.lower()
        error_patterns = {
            "i am unable to analyze": "К сожалению, мне не удалось проанализировать изображение. Убедитесь, что ладони четко видны.",
            "i cannot analyze": "К сожалению, мне не удалось проанализировать изображение. Убедитесь, что ладони четко видны.",
            "unable to process": "Не удалось обработать изображение. Убедитесь, что ладони четко видны.",
            "cannot process": "Не удалось обработать изображение. Убедитесь, что ладони четко видны.",
            "no hand detected": "На изображении не удалось распознать руку. Убедитесь, что ладони четко видны.",
            "no palm detected": "На изображении не удалось распознать ладонь. Убедитесь, что ладони четко видны.",
            "safety violation": "Изображение содержит неподходящий контент.",
            "content policy": "Изображение нарушает политику контента.",
            "rate limit": "Превышен лимит запросов. Подождите немного и попробуйте снова.",
            "quota exceeded": "Превышен лимит запросов. Подождите немного и попробуйте снова.",
        }
        for pattern, russian_message in error_patterns.items():
            if pattern in lower_response:
                ai_response_text = russian_message
                break
 
        # Автоперевод если не на русском
        if not is_russian(ai_response_text):
            ai_response_text = await translate_to_russian(ai_response_text)
 
        # Сохраняем ответ в БД
        await asyncio.get_running_loop().run_in_executor(
            db_executor, save_ai_reading_db, DATABASE_URL,
            user_id, photo_db_id, PREDEFINED_PROMPT, ai_response_text,
            first_name, last_name, telegram_username
        )
 
        # Удаляем сообщение "Идет обработка..."
        if processing_message:
            await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)
 
        # Отправляем ответ пользователю
        chunks = split_text_into_chunks(ai_response_text, TELEGRAM_MAX_MESSAGE_LENGTH)
        for i, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                await message.reply(f"📋 Часть {i} из {len(chunks)}:\n\n{chunk}")
            else:
                await message.reply(chunk)
            if i < len(chunks):
                await asyncio.sleep(1)
 
    except Exception as e:
        logging.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
        if processing_message:
            try:
                await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)
            except Exception:
                pass
        await message.reply("Произошла ошибка при анализе. Пожалуйста, попробуйте еще раз позже.")
 
@dp.message()
async def handle_unhandled_messages(message: Message):
    logging.info(f"Необработанное сообщение от {message.from_user.id}: {message.text or message.content_type}")
    await message.reply("Пожалуйста, поделитесь фотографией вашей ладони для анализа.")
 
if __name__ == "__main__":
    if not DATABASE_URL:
        logging.error("DATABASE_URL не установлен.")
        exit(1)
 
    if ADMIN_CHAT_ID:
        try:
            int(ADMIN_CHAT_ID)
        except ValueError:
            logging.error("ADMIN_CHAT_ID должен быть числом.")
            exit(1)
    else:
        logging.warning("ADMIN_CHAT_ID не установлен.")
 
    logging.info("Бот запускается...")
    try:
        init_db(DATABASE_URL)
    except Exception as e:
        logging.error(f"Не удалось инициализировать базу данных: {e}")
        exit(1)
 
    asyncio.run(dp.start_polling(bot))
    logging.info("Бот остановлен.")
