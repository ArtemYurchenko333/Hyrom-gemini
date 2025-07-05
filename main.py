import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from io import BytesIO
import base64
import google.generativeai as genai
from PIL import Image # Импортируем Image для работы с изображениями PIL

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получение токенов из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN4")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY4") # Изменено на GEMINI_API_KEY

# Инициализация бота Aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Конфигурация Gemini API
# Убедитесь, что GEMINI_API_KEY установлен в вашей среде Railway
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY не установлен. Пожалуйста, установите его в переменных окружения Railway.")
    exit(1) # Выходим, если ключ не установлен
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-2.0-flash')

# Словарь для хранения информации о запросах пользователей (например, ID фото)
user_prompts = {}

@dp.message(lambda message: message.photo)
async def handle_photo(message: Message):
    """
    Обработчик сообщений с фотографиями.
    Сохраняет file_id фотографии и просит пользователя ввести запрос.
    """
    logging.info(f"Получено фото от пользователя {message.from_user.id}")
    await message.reply("✅ Фото получено! Теперь напиши, что ты хочешь узнать по руке (например, 'расскажи про линию судьбы').")
    user_prompts[message.from_user.id] = {
        "photo": message.photo[-1].file_id # Берем самую большую версию фото
    }

@dp.message(lambda message: message.text and message.from_user.id in user_prompts)
async def handle_prompt(message: Message):
    """
    Обработчик текстовых запросов после получения фотографии.
    Загружает фото, кодирует его, отправляет в Gemini API вместе с текстом запроса
    и отправляет ответ пользователю.
    """
    user_id = message.from_user.id
    logging.info(f"Получен текстовый запрос от пользователя {user_id}")

    # Проверяем, есть ли фото для этого пользователя
    if user_id not in user_prompts or "photo" not in user_prompts[user_id]:
        await message.reply("Пожалуйста, сначала отправьте фотографию руки.")
        return

    photo_file_id = user_prompts[user_id]["photo"]
    prompt_text = message.text

    try:
        # Загрузка файла фотографии из Telegram
        file = await bot.get_file(photo_file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        file_content = file_bytes_io.read()

        # Преобразование байтов изображения в объект PIL Image
        # Gemini API ожидает объект PIL Image или байты изображения напрямую
        img = Image.open(BytesIO(file_content))

        logging.info(f"Отправка запроса в Gemini API для пользователя {user_id}")
        # Отправка запроса в Gemini API
        # Gemini Pro Vision принимает список частей: текст и изображение
        response = await model.generate_content_async(
            [prompt_text, img],
            generation_config={
                "temperature": 0.7,  # Настройте температуру для креативности (0.0-1.0)
                "top_p": 0.95,       # Настройте top_p для разнообразия
                "top_k": 0,          # Настройте top_k
                "max_output_tokens": 1024, # Максимальное количество токенов в ответе
            }
        )
        logging.info(f"Получен ответ от Gemini API для пользователя {user_id}")

        # Отправка ответа пользователю
        await message.reply(response.text)

    except Exception as e:
        logging.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
        await message.reply("Произошла ошибка при анализе руки. Пожалуйста, попробуйте еще раз позже.")
    finally:
        # Очищаем данные пользователя после обработки запроса
        if user_id in user_prompts:
            del user_prompts[user_id]

@dp.message() # Этот обработчик сработает для любого сообщения, если оно не было обработано ранее
async def handle_unhandled_messages(message: Message):
    """
    Общий обработчик для сообщений, которые не были обработаны другими хендлерами.
    Предоставляет пользователю инструкции.
    """
    logging.info(f"Необработанное сообщение от пользователя {message.from_user.id}: {message.text or message.content_type}")
    if message.text:
        await message.reply("Извините, я умею работать только с фотографиями рук. Пожалуйста, отправьте фото своей ладони, а затем напишите свой вопрос.")
    else:
        await message.reply("Извините, я могу анализировать только фотографии рук и отвечать на текстовые вопросы. Пожалуйста, отправьте фото.")


if __name__ == "__main__":
    import asyncio
    logging.info("Бот запускается...")
    asyncio.run(dp.start_polling(bot))
    logging.info("Бот остановлен.")
