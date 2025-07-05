import os
import logging
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from io import BytesIO
import google.generativeai as genai
from PIL import Image

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получение токенов из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN4")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY4")

# Инициализация бота Aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Конфигурация Gemini API
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY не установлен. Пожалуйста, установите его в переменных окружения Railway.")
    exit(1)
genai.configure(api_key=GEMINI_API_KEY)

# Модель Gemini
# Используем 'gemini-1.5-flash' как рекомендованную для скорости и мультимодальности
# Если 'gemini-2.0-flash' работает, можете оставить ее, но 'gemini-1.5-flash' - более стандартная рекомендация.
# Оставим 'gemini-2.0-flash' как в вашем файле.
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


@dp.message(lambda message: message.photo)
async def handle_photo(message: Message):
    """
    Обработчик сообщений с фотографиями.
    После получения фото показывает сообщение, ждет 3 секунды,
    затем отправляет предопределенный промт и фото в Gemini API.
    """
    user_id = message.from_user.id
    logging.info(f"Получено фото от пользователя {user_id}")

    # 1. Показываем сообщение "Спасибо..."
    processing_message = await message.reply("Спасибо за предоставленное изображение! Идет обработка...")

    # 2. Ждем 3 секунды (или сколько нужно для имитации обработки)
    await asyncio.sleep(3)

    photo_file_id = message.photo[-1].file_id # Берем самую большую версию фото
    prompt_text = PREDEFINED_PROMPT # Используем предопределенный промт

    try:
        # Загрузка файла фотографии из Telegram
        file = await bot.get_file(photo_file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        file_content = file_bytes_io.read()

        # Преобразование байтов изображения в объект PIL Image
        img = Image.open(BytesIO(file_content))

        logging.info(f"Отправка запроса в Gemini API для пользователя {user_id} с предопределенным промтом.")
        # Отправка запроса в Gemini API
        response = await model.generate_content_async(
            [prompt_text, img], # Передаем предопределенный промт и изображение
            generation_config={
                "temperature": 0.7,
                "top_p": 0.95,
                "top_k": 0,
                "max_output_tokens": 1024,
            }
        )
        logging.info(f"Получен ответ от Gemini API для пользователя {user_id}")

        # Удаляем сообщение "Идет обработка..." перед отправкой ответа
        await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)

        # Отправка ответа пользователю
        await message.reply(response.text)

    except Exception as e:
        logging.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
        # Если произошла ошибка, также удаляем сообщение о обработке, если оно еще есть
        try:
            await bot.delete_message(chat_id=processing_message.chat.id, message_id=processing_message.message_id)
        except Exception:
            pass # Игнорируем ошибку, если сообщение уже удалено или не существует
        await message.reply("Произошла ошибка при анализе руки. Пожалуйста, попробуйте еще раз позже.")

# Обработчик handle_prompt теперь не нужен, так как пользователь не вводит текстовый запрос после фото.
# Его можно удалить или оставить закомментированным.
# @dp.message(lambda message: message.text and message.from_user.id in user_prompts)
# async def handle_prompt(message: Message):
#     pass


@dp.message()
async def handle_unhandled_messages(message: Message):
    """
    Общий обработчик для сообщений, которые не были обработаны другими хендлерами.
    Предоставляет пользователю инструкции.
    """
    logging.info(f"Необработанное сообщение от пользователя {message.from
