# tg-bot-eng-teacher

Telegram-бот для изучения английского языка: диалог с LLM, коррекция ошибок и
детерминированные упражнения.

## Статус

MVP-каркас работает:

- онбординг: короткий placement-тест или ручной выбор уровня;
- чат с LLM-персоной и expandable-коррекциями;
- `/correction <текст>` — полный разбор ошибок;
- `/drill` — неправильные глаголы, mastery/streak, `/cancel`;
- `/words` — адаптивные карточки слов: study → quiz → результат, настройки и повтор ошибок;
- `/vocab` — автосохранённые слова из диалога;
- `/review` — интервальное повторение слов (упрощённый SM-2);
- `/stats` — прогресс, слабые темы и слова;
- `/roleplay` — ролевые сцены, `/stop` — выход;
- `/timezone` и `/quiet` — часовой пояс и тихие часы;
- `/delete_me` — удаление профиля и всех данных с подтверждением;
- retention диалогов 90 дней + `prune-history`;
- DB-backed напоминания с quiet hours и DST-safe переносом;
- контент в YAML с импортом в SQLite, тегами, темами и вариантами;
- coverage-отчёт и CI-валидация контента.

Ещё не реализованы: webhook, бэкапы и голос — см. локальный план в
`.opencode/_backlog.md`.

## Стек

| Слой | Технология |
| ---- | ---------- |
| Язык | Python 3.12 |
| Bot framework | aiogram 3.x |
| БД | SQLite (SQLAlchemy 2.0 async + Alembic) |
| LLM | OpenRouter (OpenAI-compatible API) |
| Пакетный менеджер | uv |
| Деплой | Docker Compose на VPS |

## Установка

Требования: Python 3.12, [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
```

## Настройка

```bash
cp .env.example .env
# заполнить BOT_TOKEN, LLM_API_KEY, LLM_MODEL_FAST, LLM_MODEL_SMART
```

## База и контент

```bash
uv run alembic upgrade head
uv run python -m app.cli import-content content/topics
```

Импорт идемпотентен: повторный запуск не создаёт дублей. Для проверки без записи:

```bash
uv run python -m app.cli import-content content/topics --dry-run
uv run python -m app.cli content-report
```

Retention диалогов по умолчанию 90 дней (`HISTORY_RETENTION_DAYS`, 0 = off),
применяется при старте и вручную:

```bash
uv run python -m app.cli prune-history
uv run python -m app.cli prune-history --days 30
```

## Запуск

```bash
uv run python -m app
```

MVP работает в режиме polling — публичный URL не требуется.
Команды бота: `/start`, `/help`, `/correction <текст>`, `/drill`, `/words`,
`/vocab`, `/review`, `/stats`, `/roleplay`, `/stop`, `/timezone`, `/quiet`,
`/delete_me`, `/cancel`.

## Docker

Образ собирается из `Dockerfile`: multi-stage, зависимости через `uv.lock`,
запуск от non-root пользователя `app` (UID 10001).

```bash
install -d -o 10001 -g 10001 data   # локально; на VDS — см. DEPLOY.md
cp .env.example .env                # заполнить BOT_TOKEN и LLM_*
docker compose build
docker compose run --rm bot python -m app.cli import-content content/topics
docker compose up -d
docker compose logs -f bot
```

Миграции применяются автоматически при старте контейнера
(`alembic upgrade head`). Данные лежат в `./data` (bind mount), поэтому
`docker compose down` их не удаляет.

Каждый бот запускается своим compose-проектом: имя `tg-bot-eng-teacher` задано
в `docker-compose.yml`, контейнеры и сети изолированы от других ботов на VDS.
Лимиты по умолчанию — `320m` RAM и `0.8` CPU; при нехватке ресурсов меняйте
`mem_limit`/`cpus`.

Бэкап SQLite:

```bash
docker compose exec bot sh -c \
  'sqlite3 /app/data/bot.db ".backup /app/data/backup-$(date +%F).db"'
```

## CI/CD

Релиз запускается тегом `vX.Y.Z`: GitHub Actions проверяет, что тег есть в
`main`, заходит по SSH на VDS, собирает образ **на самом VDS** и перезапускает
контейнер. Перед сборкой `scripts/deploy.sh` делает бэкап SQLite и хранит
последние 10 файлов в `backups/`.

Полная пошаговая инструкция — **[DEPLOY.md](DEPLOY.md)**: подготовка VDS,
swap, пользователь `deploy`, forced-command ключ, секреты GitHub, первый релиз,
проверка, откат, бэкапы и диагностика.

Коротко:

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
gh run watch
```

## Тесты и проверки

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run python -m app.cli import-content content/topics --dry-run
```

## Документация

Проектная документация ведётся в локальном OKF-бандле `.opencode/`
(не коммитится в репозиторий):

- `.opencode/AGENTS.md` — точка входа
- `.opencode/_concepts.md` — архитектура
- `.opencode/_authoring.md` — правила добавления контента
- `.opencode/_setup.md` — локальная установка
- `.opencode/_backlog.md` — план работ

## Лицензия

Пока не выбрана. Учебный контент помечен `license: unknown`; аудит происхождения
и условия для публичного релиза — в `.opencode/analysis/2026-10-29-license-audit.md`
(B-028). До решения не распространяйте контент как открытый.
