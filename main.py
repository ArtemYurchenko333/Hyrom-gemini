import os
import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message, User, BufferedInputFile # Импортируем BufferedInputFile
from io import BytesIO
import google.generativeai as genai
from PIL import Image

import psycopg2
from psycopg2 import sql
from concurrent.futures import ThreadPoolExecutor

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получение токенов из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN4")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY4")
DATABASE_URL = os.getenv("DATABASE_URL4")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID") # Получаем ID администратора

# Инициализация бота Aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Конфигурация Gemini API
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY не установлен. Пожалуйста, установите его в переменных окружения Railway.")
    exit(1)
genai.configure(api_key=GEMINI_API_KEY)

# Модель Gemini
model = genai.GenerativeModel('gemini-2.0-flash')

# Ваш предопределенный промт
PREDEFINED_PROMPT = """Проанализируй ладони по предоставленным фотографиям, используя опыт проессиональной хиромантии.

Обрати внимание на форму кисти, длину и пропорции пальцев, текстуру кожи, рисунок линий (жизни, сердца, ума, судьбы) и их взаимодействие.

Составь глубокий и структурированный портрет моей личности: опиши сильные и слабые стороны характера, особенности мышления, эмоциональную сферу, карьерный потенцыал, природные таланты и вероятные жизненные вызовы.
Отдельно сделай акцент на:
- ключевые моменты, когда возможны важные повороты судьбы,
- скрытые ресурсы, которые стоит развивать,
- рекомендации для гармоничного раскрытия моих способностей.

Пиши професссонально, с опорой на системный подход и лучшие практики анализа ладони. Избегай общих фраз - стремись к конкретике и точности"""

# --- Функции для работы с базой данных ---

# Создаем пул потоков для выполнения блокирующих операций с БД
db_executor = ThreadPoolExecutor()

def get_db_connection(db_url):
    """Устанавливает и возвращает соединение с базой данных."""
    if not db_url:
        raise ValueError("DATABASE_URL не установлен.")
    return psycopg2.connect(db_url)

def init_db(db_url):
    """Инициализирует таблицы в базе данных, если они не существуют."""
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        # Таблица пользователей
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                telegram_username VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Таблица для фотографий ладоней
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
        # Таблица для ответов хиромантии
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
    """Получает пользователя из БД или создает нового, если его нет."""
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE id = %s", (user.id,))
        existing_user = cur.fetchone()

        if not existing_user:
            cur.execute(
                """
                INSERT INTO users (id, telegram_username, first_name, last_name)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (user.id, user.username, user.first_name, user.last_name)
            )
            user_id = cur.fetchone()[0]
            conn.commit()
            logging.info(f"Новый пользователь {user_id} ({user.username}) добавлен в базу данных.")
        else:
            user_id = existing_user[0]
            logging.info(f"Пользователь {user_id} ({user.username}) уже существует в базе данных.")
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
    """Сохраняет информацию о фотографии ладони в БД, включая данные пользователя."""
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO palm_photos (user_id, telegram_file_id, first_name, last_name, telegram_username)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (user_id, telegram_file_id, first_name, last_name, telegram_username)
        )
        photo_id = cur.fetchone()[0]
        conn.commit()
        logging.info(f"Информация о фото (file_id: {telegram_file_id}) для пользователя {user_id} сохранена с photo_id {photo_id}.")
        return photo_id
    except Exception as e:
        logging.error(f"Ошибка при сохранении информации о фото для пользователя {user_id}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def save_ai_reading_db(db_url: str, user_id: int, photo_id: int, prompt_text: str, ai_response: str, first_name: str, last_name: str, telegram_username: str):
    """Сохраняет ответ ИИ по хиромантии в БД, включая данные пользователя."""
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO palm_readings (user_id, photo_id, prompt_text, ai_response, first_name, last_name, telegram_username)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (user_id, photo_id, prompt_text, ai_response, first_name, last_name, telegram_username)
        )
        conn.commit()
        logging.info(f"Ответ ИИ для пользователя {user_id} (photo_id: {photo_id}) сохранен.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении ответа ИИ для пользователя {user_id}: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

async def download_file_by_id(file_id: str) -> BytesIO:
    """
    Загружает файл из Telegram по его file_id и возвращает его содержимое
    в виде объекта BytesIO.
    """
    try:
        file_info = await bot.get_file(file_id)
        if not file_info.file_path:
            raise ValueError(f"Не удалось получить file_path для file_id: {file_id}")

        file_content_bytes_io = await bot.download_file(file_info.file_path)
        logging.info(f"Файл с file_id {file_id} успешно загружен.")
        return file_content_bytes_io
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла по file_id {file_id}: {e}")
        raise


# --- Обработчики сообщений бота ---

@dp.message(lambda message: message.photo)
async def handle_photo(message: Message):
    """
    Обработчик сообщений с фотографиями.
    После получения фото показывает сообщение, ждет 3 секунды,
    затем отправляет предопределенный промт и фото в Gemini API,
    а также сохраняет данные в БД, включая имя, фамилию и ник.
    Если установлен ADMIN_CHAT_ID, отправляет фото администратору.
    """
    user_id = message.from_user.id
    telegram_user = message.from_user
    processing_message = None
    ai_response_text = "" # Инициализируем на случай ошибок до получения ответа

    try:
        logging.info(f"Получено фото от пользователя {user_id}")

        # Получаем или создаем пользователя в БД
        await asyncio.get_running_loop().run_in_executor(
            db_executor,
            get_or_create_user_db,
            DATABASE_URL,
            telegram_user
        )

        first_name = telegram_user.first_name if telegram_user.first_name else ""
        last_name = telegram_user.last_name if telegram_user.last_name else ""
        telegram_username = telegram_user.username if telegram_user.username else ""

        processing_message = await message.reply("Спасибо за предоставленное изображение! Идет обработка...")

        await asyncio.sleep(3) # Задержка для имитации "обработки"

        photo_file_id = message.photo[-1].file_id
        prompt_text = PREDEFINED_PROMPT

        # Сохраняем информацию о фото в БД
        photo_db_id = await asyncio.get_running_loop().run_in_executor(
            db_executor,
            save_photo_info_db,
            DATABASE_URL,
            user_id,
            photo_file_id,
            first_name,
            last_name,
            telegram_username
        )

        # Отправка фото администратору
        if ADMIN_CHAT_ID:
            try:
                file_for_admin_bytes_io = await download_file_by_id(photo_file_id)
                buffered_file_for_admin = BufferedInputFile(
                    file=file_for_admin_bytes_io.getvalue(),
                    filename=f"palm_photo_{user_id}_{photo_db_id}.jpg"
                )
                caption_for_admin = (
                    f"Новое фото ладони от пользователя:\n"
                    f"ID: {user_id}\n"
                    f"Имя: {first_name} {last_name}\n"
                    f"Ник: @{telegram_username if telegram_username else 'нет'}"
                )
                await bot.send_photo(
                    chat_id=ADMIN_CHAT_ID,
                    photo=buffered_file_for_admin,
                    caption=caption_for_admin
                )
                logging.info(f"Фото от пользователя {user_id} отправлено администратору {ADMIN_CHAT_ID}.")
            except Exception as admin_send_error:
                logging.error(f"Не удалось отправить фото администратору {ADMIN_CHAT_ID}: {admin_send_error}")


        # Загрузка файла фотографии из Telegram и отправка в Gemini API
        file = await bot.get_file(photo_file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        file_content = file_bytes_io.read()
        img = Image.open(BytesIO(file_content))

        logging.info(f"Отправка запроса в Gemini API для пользователя {user_id} с предопределенным промтом.")
        response = await model.generate_content_async(
            [prompt_text, img],
            generation_config={
                "temperature": 0.7,
                "top_p": 0.95,
                "top_k": 0,
                "max_output_tokens": 800, # Установлено 800 токенов для одного сообщения
            }
        )
        ai_response_text = response.text
        logging.info(f"Получен ответ от Gemini API для пользователя {user_id}")

        try:
    	    ai_response_text = response.text.strip()
    	    # Примитивная фильтрация ошибок по ключевым словам
	    lower_response = ai_response_text.lower()
	    if "cat" in lower_response or "animal" in lower_response:
	        ai_response_text = "К сожалению, изображение содержит животное. Пожалуйста, загрузите фото ладони."
	    elif "unsupported" in lower_response or "not clearly visible" in lower_response or "image" in lower_response:
	        ai_response_text = "К сожалению, изображение не удалось распознать. Пожалуйста, загрузите четкое фото ладони."
	    elif not ai_response_text:
	        ai_response_text = "К сожалению, анализ не удался. Пожалуйста, попробуйте позже с другим фото."
	except Exception as e:
	    logging.error(f"Ошибка при извлечении текста Gemini: {e}")
	    ai_response_text = "Возникла ошибка при получении ответа от ИИ. Попробуйте позже."


        # Сохраняем ответ ИИ в БД (уже обработанный текст)
        await asyncio.get_running_loop().run_in_executor(
            db_executor,
            save_ai_reading_db,
            DATABASE_URL,
            user_id,
            photo_db_id,
            prompt_text,
            ai_response_text,
            first_name,
            last_name,
            telegram_username
        )

        # Удаляем сообщение "Идет обработка..." перед отправкой ответа
        if processing_message:
            await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)

        # Отправляем ответ пользователю, разбив, если длинный
	MAX_MESSAGE_LENGTH = 4096
	if len(ai_response_text) > MAX_MESSAGE_LENGTH:
	    for i in range(0, len(ai_response_text), MAX_MESSAGE_LENGTH):
        	await message.reply(ai_response_text[i:i + MAX_MESSAGE_LENGTH])
	else:
	    await message.reply(ai_response_text)


    except Exception as e:
        logging.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
        if processing_message:
            try:
                await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)
            except Exception:
                pass
        # Общая ошибка, отправляемая пользователю на русском
        await message.reply("Произошла ошибка при анализе руки. Пожалуйста, попробуйте еще раз позже.")


@dp.message()
async def handle_unhandled_messages(message: Message):
    """
    Общий обработчик для сообщений, которые не были обработаны другими хендлерами.
    Предоставляет пользователю инструкции.
    """
    logging.info(f"Необработанное сообщение от пользователя {message.from_user.id}: {message.text or message.content_type}")
    # Сообщение-инструкция для пользователя на русском
    await message.reply("Пожалуйста, поделитесь фотографией вашей ладони для анализа. Я могу анализировать только изображения рук.")


if __name__ == "__main__":
    if not DATABASE_URL:
        logging.error("DATABASE_URL не установлен. Пожалуйста, установите его в переменных окружения.")
        exit(1)

    if ADMIN_CHAT_ID:
        try:
            # Проверяем, что ADMIN_CHAT_ID является числом
            int(ADMIN_CHAT_ID)
        except ValueError:
            logging.error("ADMIN_CHAT_ID должен быть числовым ID чата. Проверьте переменную окружения.")
            exit(1)
    else:
        logging.warning("ADMIN_CHAT_ID не установлен. Фотографии не будут отправляться администратору.")


    logging.info("Бот запускается...")
    try:
        init_db(DATABASE_URL) # Инициализация базы данных
    except Exception as e:
        logging.error(f"Не удалось инициализировать базу данных: {e}. Бот не будет запущен.")
        exit(1)

    # Запуск бота в режиме long polling
    asyncio.run(dp.start_polling(bot))
    logging.info("Бот остановлен.")
