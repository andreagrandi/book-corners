FROM node:25-alpine AS css-builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build:css

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG SOURCE_VERSION=""
ENV GIT_REV=${SOURCE_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    gettext \
    libc6-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .
COPY --from=css-builder /app/static/css/app.css /app/static/css/app.css

RUN python manage.py compilemessages
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--config", "gunicorn.conf.py"]
