# tg-bot-eng-teacher

Telegram-бот для изучения английского языка: диалог с LLM, коррекция ошибок и
детерминированные упражнения.

## Статус

MVP-каркас работает:

- онбординг: короткий placement-тест или ручной выбор уровня;
- чат с LLM-персоной и expandable-коррекциями;
- rolling summary длинного диалога, watermark и `/new`; cap на 200 сообщений;
- LLM routing fast/smart/fallback, repair JSON, лимит сообщения и typing-индикатор;
- глобальный месячный USD-бюджет ($2), price table и алерты 80/100% в админ-чат;
- LLM-судья для переводов класса B только при расхождении с эталоном;
- correlation id, runtime-метрики и `app.cli metrics`, опциональный Sentry;
- продуктовые метрики: DAU/WAU/MAU, D1/D7 retention, completion сессий и
  onboarding — `app.cli product-metrics`;
- `/translate` — банк 100 фраз B1, deterministic-проверка + judge на mismatch;
- `/correction <текст>` — полный разбор ошибок;
- `/menu` — разделы «Учёба», «Практика», «Прогресс», «Настройки»;
- `/drill` — неправильные глаголы, mastery/streak, выход через `/cancel` или кнопку;
- `/words` — адаптивные карточки слов: study → quiz → результат, настройки и повтор ошибок;
- `/vocab` — автосохранённые слова из диалога;
- `/review` — интервальное повторение слов (упрощённый SM-2);
- `/stats` — прогресс, слабые темы и слова;
- `/roleplay` — ролевые сцены, компактные поправки и полный разбор после `/stop`;
- `/goals` — цели, интересы и дневная норма; учитываются в промпте и `/drill`;
- курируемая база знаний: отобранные админом объяснения с тегами по целям и
  интересам; до 2 штук попадают в промпт, без обмена чужими диалогами;
- `/timezone` и `/quiet` — часовой пояс и тихие часы;
- `/about_me` — что бот хранит о пользователе (профиль и счётчики данных);
- `/delete_me` — удаление профиля и всех данных с подтверждением;
- retention диалогов 90 дней + `prune-history`;
- DB-backed напоминания с quiet hours и DST-safe переносом;
- контент в YAML с импортом в SQLite и единым форматом `items`;
- банк слов: 6166 слов в `content/vocabulary/` — 2992 частотных с
  транскрипцией и 3174 дополнительных без частоты; CEFR не хранится;
- coverage-отчёт и CI-валидация контента;
- отдельный админ-бот: `/admin`, `/health`, `/users`, `/user`, `/block`,
  `/mute`, `/unblock`, `/audit`; общая БД, свой токен и allowlist;
- `app.cli health` + heartbeat + Docker healthcheck + внешний uptime-пинг
  (dead-man's switch) и опциональный Sentry;
- `admin_audit` и containment (`block`/`mute`) в пользовательском боте.

Ещё не реализованы: webhook, голос, VPS-мониторинг (uptime вынесен на внешний
сервис) — см. локальный план в `.opencode/_backlog.md`.

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
# для админ-бота: ADMIN_BOT_TOKEN и ADMIN_IDS (см. .opencode/_admin.md)
```

## База и контент

Контент разделён на три слоя:

- `content/topics/` — упражнения (глаголы, gap-fill, placement);
- `content/vocabulary/` — банк слов (слово и перевод; частота и транскрипция
  опциональны) в том же формате `topic` + `items`;
- `content/knowledge/` — курируемые объяснения с тегами целей/интересов; в
  промпт попадают только совпавшие, максимум 2.

```bash
uv run alembic upgrade head
uv run python -m app.cli seed
```

`seed` импортирует все слои и идемпотентен: повторный запуск не создаёт дублей.
Точечные команды остаются доступны:

```bash
uv run python -m app.cli import-content content/topics
uv run python -m app.cli import-content content/vocabulary
uv run python -m app.cli import-content content/knowledge
```

Для проверки без записи:

```bash
uv run python -m app.cli seed --dry-run
uv run python -m app.cli content-report
uv run python -m app.cli llm-report          # токены, стоимость, latency
uv run python -m app.cli eval                # golden-диалоги: replay + baseline
uv run python -m app.cli metrics             # runtime counters/timers
uv run python -m app.cli product-metrics     # DAU, retention, completion
```

Бот читает только SQLite; YAML — источник истины для git-ревью. Подробнее о
пайплайне — в `.opencode/_content_pipeline.md`.

Retention диалогов по умолчанию 90 дней (`HISTORY_RETENTION_DAYS`, 0 = off),
применяется при старте и вручную:

```bash
uv run python -m app.cli prune-history
uv run python -m app.cli prune-history --days 30
```

## Сохранение и сброс БД

`data/bot.db` **не обнуляется** при обычном рестарте, `docker compose down`
или пересборке образа. На старте применяются только новые миграции
(`alembic upgrade head`) и идемпотентный `seed`.

Для явных операций есть `scripts/db.sh`:

```bash
./scripts/db.sh status           # revision, integrity, счётчики
./scripts/db.sh backup           # бэкап в backups/, хранит 10 файлов
./scripts/db.sh migrate          # backup -> миграции -> seed -> рестарт
./scripts/db.sh reset --yes      # backup -> удалить БД -> миграции+seed
./scripts/db.sh restore backups/pre-reset-....db
./scripts/db.sh prune            # деактивировать контент, убранный из YAML
./scripts/db.sh drill            # проверить последний бэкап, не трогая рабочую БД
```

Off-site копия (нужны R2-креды и `rclone`; без них доступен `--dry-run`):

```bash
BACKUP_REMOTE=r2:my-bucket/backups ./scripts/backup_r2.sh --dry-run
BACKUP_REMOTE=r2:my-bucket/backups ./scripts/backup_r2.sh
```

`reset` без `--yes` отказывает; при `APP_ENV=production` требует `--force`.
Перед `migrate`/`reset`/`restore` бэкап делается автоматически. Подробный
сценарий, миграции и recovery — в `.opencode/_db_operations.md`.

## Запуск

```bash
uv run python -m app
```

MVP работает в режиме polling — публичный URL не требуется.
В меню бота: `/start`, `/menu`, `/stats`, `/help`. Остальные
команды (`/drill`, `/words`, `/review`, `/vocab`, `/correction`, `/roleplay`,
`/stop`, `/new`, `/goals`, `/timezone`, `/quiet`, `/about_me`, `/delete_me`)
сгруппированы в `/menu`.

## Docker

Образ собирается из `Dockerfile`: multi-stage, зависимости через `uv.lock`,
запуск от non-root пользователя `app` (UID 10001).

```bash
install -d -o 10001 -g 10001 data   # локально; на VDS — см. DEPLOY.md
cp .env.example .env                # заполнить BOT_TOKEN и LLM_*
docker compose build
docker compose up -d
docker compose logs -f bot
docker compose --profile admin up -d   # если настроен ADMIN_BOT_TOKEN
```

Миграции и seed выполняет one-shot сервис `migrate`; `bot` и `admin-bot`
стартуют после него. Данные лежат в `./data` (bind mount), поэтому
`docker compose down` их не удаляет.

Проверка здоровья:

```bash
docker compose exec bot python -m app.cli health
```

Мониторинг (подробно — `DEPLOY.md`, раздел 12):

- `UPTIME_PING_URL` — dead-man's switch (healthchecks.io/UptimeRobot). Бот
  пингует URL после каждого heartbeat; тишина → внешний алерт.
- `python -m app.cli uptime` — разовая проверка URL.
- `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE` — ошибки и алерты;
  `send_default_pii=False`, request-данные вырезаются.
- `python -m app.cli metrics` / `product-metrics` — runtime и продуктовые
  метрики.

Каждый бот запускается своим compose-проектом: имя `tg-bot-eng-teacher` задано
в `docker-compose.yml`, контейнеры и сети изолированы от других ботов на VDS.
Лимиты по умолчанию — `320m` RAM и `0.8` CPU; при нехватке ресурсов меняйте
`mem_limit`/`cpus`.

Бэкап SQLite:

```bash
./scripts/db.sh backup
```

Восстановление и сброс — там же, либо вручную через `sqlite3 .backup`
(см. `.opencode/_db_operations.md`).

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
