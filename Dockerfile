FROM python:3.12-slim

# fonts-dejavu-core нужен для кириллицы на печатных плакатах и метках карты:
# без него подписи отрисуются квадратами.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data media

CMD ["python", "-m", "app.bot.main"]
