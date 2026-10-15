# GamePulse

Сервис сбора игр с Metacritic с каталогом, анализом отзывов и мониторингом фоновой обработки.

## Требования

Выберите один из вариантов запуска:

- **Docker:** Docker Engine или Docker Desktop с поддержкой Docker Compose.
- **Без Docker:** Python 3.12 и SQLite, которая входит в стандартную библиотеку Python.

Все команды выполняются из корня репозитория. Для сбора данных нужен доступ к Metacritic; для AI-анализа и поиска видео — ключи соответствующих API.

## Настройка окружения

Скопируйте шаблон конфигурации:

```bash
# Linux / macOS
cp .env.example .env
```

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

Отредактируйте `.env`:

| Переменная | Назначение |
| --- | --- |
| `LLM_API_KEY` | Ключ OpenAI для анализа отзывов и расшифровок |
| `LLM_MODEL` | Модель анализа; по умолчанию `gpt-5-nano` |
| `YOUTUBE_API_KEY` | Ключ проекта с включённым YouTube Data API v3 |
| `DATABASE_URL` | При запуске без Docker: `sqlite:///./gamepulse.db` |
| `TIMEZONE` | Часовой пояс суточного обхода; по умолчанию `Europe/Moscow` |
| `INTERVAL_SECONDS` | Интервал обработки; по умолчанию `3600` секунд |
| `COOKIE_SECURE` | Для локального HTTP оставьте `false` |

Без `LLM_API_KEY` приложение работает без AI-анализа. Без `YOUTUBE_API_KEY` поиск летсплеев отключён. `ADMIN_TOKEN` нужен только для внешних клиентов API; для ручного запуска из веб-интерфейса достаточно входа в аккаунт.

Файлы `.env` и `.local-credentials.txt` содержат конфиденциальные данные и исключены из Git.

## Запуск через Docker

Compose запускает PostgreSQL, веб-приложение и фоновый воркер.

Соберите образ и создайте аккаунт. Команда создания аккаунта записывает настройки в локальный `.env` через подключённую директорию проекта:

```bash
docker compose build
docker compose run --rm --no-deps --user root -v "${PWD}:/setup" -w /setup web python -m app.setup_login
docker compose up -d --wait
```

Команды подходят для Bash и PowerShell. Логин — `admin`, сгенерированный пароль сохранён в `.local-credentials.txt`. Повторный вызов `app.setup_login` создаёт новый пароль. Одинарные кавычки вокруг `WEB_PASSWORD_HASH` в `.env` нужно сохранить.

Схема БД создаётся автоматически. PostgreSQL и обложки хранятся в постоянных Docker volumes и сохраняются после остановки контейнеров.

Проверка состояния, просмотр логов и остановка:

```bash
docker compose ps
docker compose logs -f web worker
docker compose down
```

После изменения `.env` примените настройки повторным вызовом `docker compose up -d`. Для пересборки после изменения кода используйте `docker compose up -d --build`.

## Запуск без Docker

Создайте виртуальное окружение, установите зависимости и создайте аккаунт.

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps -e .
.\.venv\Scripts\python -m app.setup_login
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

В отдельном терминале из корня проекта запустите воркер:

```powershell
.\.venv\Scripts\python -m app.jobs
```

**Linux / macOS:**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m app.setup_login
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

В отдельном терминале из корня проекта:

```bash
.venv/bin/python -m app.jobs
```

При первом запуске автоматически создаются файл `gamepulse.db`, таблицы и каталог обложек `data/covers`. Логин — `admin`, пароль находится в `.local-credentials.txt`. После изменения `.env` перезапустите оба процесса. Для остановки нажмите `Ctrl+C` в каждом терминале.

## Проверка работы

- Вход и каталог: [http://127.0.0.1:8000](http://127.0.0.1:8000).
- Мониторинг и ручной запуск: [http://127.0.0.1:8000/monitor](http://127.0.0.1:8000/monitor).
- Документация API: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
- Проверка доступности: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

Веб-приложение и воркер работают отдельно: запуск только веб-приложения не запускает сбор данных. Первый обход начинается после старта воркера, последующие — раз в час. В новом каталоге игры появляются по мере обработки; состояние запуска доступно на странице мониторинга.

## Локальные проверки

В виртуальном окружении выполните:

```bash
python -m pytest -q
python -m ruff check app tests
python -m ruff format --check app tests
```

В Windows используйте `.\.venv\Scripts\python` вместо `python`, в Linux / macOS — `.venv/bin/python`. По умолчанию тесты используют отдельную SQLite и не требуют ключей внешних API.
