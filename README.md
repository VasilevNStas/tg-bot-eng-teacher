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
install -d -o 10001 -g 10001 data   # один раз, нужен root
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

Релиз запускается тегом `vX.Y.Z`: GitHub Actions заходит по SSH на VDS, делает
`git fetch`, собирает образ **на самом VDS** и перезапускает контейнер.
Перед сборкой скрипт `scripts/deploy.sh` делает `sqlite3 .backup` и хранит
последние 10 бэкапов в `backups/`.

Тег должен указывать на коммит из `main` (там уже требуется зелёный CI).

### One-time setup на VDS

Как root:

```bash
useradd --create-home --shell /bin/bash --groups docker deploy
install -d -o deploy -g deploy /srv/tg-bot-eng-teacher
sudo -u deploy git clone https://github.com/VasilevNStas/tg-bot-eng-teacher.git \
  /srv/tg-bot-eng-teacher
install -d -o 10001 -g 10001 /srv/tg-bot-eng-teacher/data
sudo -u deploy cp /srv/tg-bot-eng-teacher/.env.example \
  /srv/tg-bot-eng-teacher/.env
# заполнить .env: BOT_TOKEN, LLM_API_KEY, LLM_MODEL_FAST, LLM_MODEL_SMART
```

SSH-ключ для деплоя (на локальной машине):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/tg_bot_eng_teacher_deploy -N "" -C "gha-deploy"
```

Публичный ключ на VDS (root), с forced command:

```bash
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
printf 'command="bash /srv/tg-bot-eng-teacher/scripts/deploy.sh",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding %s\n' \
  "$(cat tg_bot_eng_teacher_deploy.pub)" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
```

Секреты GitHub:

```bash
gh secret set VDS_HOST --body "193.168.49.204"
gh secret set VDS_USER --body "deploy"
gh secret set VDS_SSH_KEY < ~/.ssh/tg_bot_eng_teacher_deploy
ssh-keyscan -H 193.168.49.204 | gh secret set VDS_KNOWN_HOSTS
```

### Выпуск релиза

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
gh run watch
```

Повторный деплой без нового тега:

```bash
gh workflow run release.yml -f tag=v0.1.0
```

Первый деплой создаёт БД и применяет миграции автоматически
(`alembic upgrade head` в команде контейнера).

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
