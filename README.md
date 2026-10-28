# tg-bot-eng-teacher

Telegram-бот для изучения английского языка: диалог с LLM, коррекция ошибок и
детерминированные упражнения (неправильные глаголы, переводы, SRS).

> Статус: инициализация проекта. Код ещё не реализован — см. план в
> `.opencode/_backlog.md`.

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

```bash
# [TODO: clarify with the user] — команды появятся после фазы 0
```

## Настройка

```bash
cp .env.example .env
# заполнить BOT_TOKEN и LLM_API_KEY
```

## Запуск

```bash
# [TODO: clarify with the user] — появятся после фазы 0
```

## Тесты

```bash
# [TODO: clarify with the user]
```

## Документация

Проектная документация ведётся в локальном OKF-бандле `.opencode/`
(не коммитится в репозиторий):

- `.opencode/AGENTS.md` — точка входа
- `.opencode/_concepts.md` — архитектура
- `.opencode/_setup.md` — локальная установка
- `.opencode/_backlog.md` — план работ

## Лицензия

[TODO: clarify with the user]
