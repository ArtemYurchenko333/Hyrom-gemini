import os
import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message, User
from io import BytesIO
import google.generativeai as genai
from PIL import Image

import psycopg2 # Импортируем библиотеку для работы с PostgreSQL
from psycopg2 import sql
from concurrent.futures import ThreadPoolExecutor # Для асинхронных операций с БД

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получение токенов из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN4")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY4")
DATABASE_URL = os.getenv("DATABASE_URL4") # Переменная окружения для подключения к БД

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
                read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logging.info("Таблицы базы данных проверены/созданы успешно.")
    except Exception as e:
        logging.error(f"Ошибка при инициализации базы данных: {e}")
        if conn:
            conn.rollback() # Откатываем транзакцию в случае ошибки
        raise # Перевыбрасываем исключение, чтобы остановить запуск бота, если БД недоступна
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

def save_photo_info_db(db_url: str, user_id: int, telegram_file_id: str) -> int:
    """Сохраняет информацию о фотографии ладони в БД."""
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO palm_photos (user_id, telegram_file_id)
            VALUES (%s, %s)
            RETURNING id;
            """,
            (user_id, telegram_file_id)
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

def save_ai_reading_db(db_url: str, user_id: int, photo_id: int, prompt_text: str, ai_response: str):
    """Сохраняет ответ ИИ по хиромантии в БД."""
    conn = None
    try:
        conn = get_db_connection(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO palm_readings (user_id, photo_id, prompt_text, ai_response)
            VALUES (%s, %s, %s, %s);
            """,
            (user_id, photo_id, prompt_text, ai_response)
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

# --- Обработчики сообщений бота ---

@dp.message(lambda message: message.photo)
async def handle_photo(message: Message):
    """
    Обработчик сообщений с фотографиями.
    После получения фото показывает сообщение, ждет 3 секунды,
    затем отправляет предопределенный промт и фото в Gemini API,
    а также сохраняет данные в БД.
    """
    user_id = message.from_user.id
    telegram_user = message.from_user
    processing_message = None # Инициализируем на случай ошибки до его создания

    try:
        logging.info(f"Получено фото от пользователя {user_id}")

        # 1. Получаем или создаем пользователя в БД
        # Выполняем синхронную операцию в отдельном потоке
        await asyncio.get_running_loop().run_in_executor(
            db_executor,
            get_or_create_user_db,
            DATABASE_URL,
            telegram_user
        )

        # 2. Показываем сообщение "Спасибо..."
        processing_message = await message.reply("Спасибо за предоставленное изображение! Идет обработка...")

        # 3. Ждем 3 секунды (или сколько нужно для имитации обработки)
        await asyncio.sleep(3)

        photo_file_id = message.photo[-1].file_id # Берем самую большую версию фото
        prompt_text = PREDEFINED_PROMPT # Используем предопределенный промт

        # 4. Сохраняем информацию о фото в БД
        # Выполняем синхронную операцию в отдельном потоке
        photo_db_id = await asyncio.get_running_loop().run_in_executor(
            db_executor,
            save_photo_info_db,
            DATABASE_URL,
            user_id,
            photo_file_id
        )

        # 5. Загрузка файла фотографии из Telegram и отправка в Gemini API
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
                "max_output_tokens": 1024,
            }
        )
        ai_response_text = response.text
        logging.info(f"Получен ответ от Gemini API для пользователя {user_id}")

        # 6. Сохраняем ответ ИИ в БД
        # Выполняем синхронную операцию в отдельном потоке
        await asyncio.get_running_loop().run_in_executor(
            db_executor,
            save_ai_reading_db,
            DATABASE_URL,
            user_id,
            photo_db_id,
            prompt_text,
            ai_response_text
        )

        # 7. Удаляем сообщение "Идет обработка..." перед отправкой ответа
        if processing_message:
            await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)

        # 8. Отправка ответа пользователю
        await message.reply(ai_response_text)

    except Exception as e:
        logging.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
        # Если произошла ошибка, также удаляем сообщение о обработке, если оно еще есть
        if processing_message:
            try:
                await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)
            except Exception:
                pass # Игнорируем ошибку, если сообщение уже удалено или не существует
        await message.reply("Произошла ошибка при анализе руки. Пожалуйста, попробуйте еще раз позже.")


@dp.message()
async def handle_unhandled_messages(message: Message):
    """
    Общий обработчик для сообщений, которые не были обработаны другими хендлерами.
    Предоставляет пользователю инструкции.
    """
    logging.info(f"Необработанное сообщение от пользователя {message.from_user.id}: {message.text or message.content_type}")
    if message.text:
        await message.reply("Извините, я умею работать только с фотографиями рук. Пожалуйста, отправьте фото своей ладони.")
    else:
        await message.reply("Извините, я могу анализировать только фотографии рук. Пожалуйста, отправьте фото.")


if __name__ == "__main__":
    # Проверяем наличие DATABASE_URL перед запуском
    if not DATABASE_URL:
        logging.error("DATABASE_URL не установлен. Пожалуйста, установите его в переменных окружения.")
        exit(1)

    logging.info("Бот запускается...")
    # Инициализируем БД при запуске бота
    try:
        init_db(DATABASE_URL)
    except Exception as e:
        logging.error(f"Не удалось инициализировать базу данных: {e}. Бот не будет запущен.")
        exit(1)

    asyncio.run(dp.start_polling(bot))
    logging.info("Бот остановлен.")
