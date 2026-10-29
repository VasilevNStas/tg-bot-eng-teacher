# tg-bot-eng-teacher

Telegram-бот для изучения английского языка: диалог с LLM, коррекция ошибок и
детерминированные упражнения.

## Статус

MVP-каркас работает:

- онбординг: короткий placement-тест или ручной выбор уровня;
- чат с LLM-персоной и expandable-коррекциями;
- `/correction <текст>` — полный разбор ошибок;
- `/drill` — неправильные глаголы, mastery/streak, `/cancel`;
- `/vocab` — автосохранённые слова из диалога;
- `/review` — интервальное повторение слов (упрощённый SM-2);
- `/stats` — прогресс, слабые темы и слова;
- `/roleplay` — ролевые сцены, `/stop` — выход;
- `/timezone` и `/quiet` — часовой пояс и тихие часы;
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

## Запуск

```bash
uv run python -m app
```

MVP работает в режиме polling — публичный URL не требуется.
Команды бота: `/start`, `/help`, `/correction <текст>`, `/drill`, `/vocab`,
`/review`, `/stats`, `/roleplay`, `/stop`, `/timezone`, `/quiet`, `/cancel`.

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
