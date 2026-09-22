# Используем Python 3.13, так как именно он был в логах Bothost
FROM python:3.13-slim

# Настраиваем переменные окружения
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/app/data

# Устанавливаем рабочую директорию
WORKDIR /app

# Создаем виртуальное окружение (как в логах Bothost)
RUN python -m venv /opt/venv

# Копируем только requirements.txt для лучшего кэширования
COPY requirements.txt .

# Обновляем pip и устанавливаем все зависимости из requirements.txt
# Теперь maxapi и все остальные библиотеки точно попадут в /opt/venv
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код проекта
COPY . .

# Создаем папку для данных и выдаем права (как в ваших логах, шаги 18-19)
RUN mkdir -p /app/data && chmod 777 /app/data

# Открываем порт (если ваш бот использует вебхуки или FastAPI)
# Bothost обычно сам прокидывает порт, но это не помешает
EXPOSE 3000

# Команда для запуска бота
CMD ["python", "main.py"]