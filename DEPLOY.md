# Деплой и CI/CD

Пошаговая инструкция: от пустого VDS до автоматического релиза по тегу.
Проект рассчитан на **один VDS с несколькими ботами**: каждый бот — отдельный
Docker Compose проект со своим `.env`, своей БД и своим контейнером.

## Содержание

1. [Как это работает](#1-как-это-работает)
2. [Требования](#2-требования)
3. [Подготовка VDS](#3-подготовка-vds)
4. [SSH-ключ для деплоя](#4-ssh-ключ-для-деплоя)
5. [Секреты GitHub](#5-секреты-github)
6. [Первый релиз](#6-первый-релиз)
7. [Проверка после деплоя](#7-проверка-после-деплоя)
8. [Следующие релизы](#8-следующие-релизы)
9. [Ручной деплой](#9-ручной-деплой)
10. [Откат](#10-откат)
11. [Бэкапы и восстановление](#11-бэкапы-и-восстановление)
12. [Диагностика](#12-диагностика)
13. [Безопасность](#13-безопасность)
14. [Чек-лист](#14-чек-лист)

---

## 1. Как это работает

```
git push origin v0.1.0
        │
        ▼
GitHub Actions (release.yml)
  ├─ проверяет, что тег vX.Y.Z и он есть в main
  └─ ssh deploy@VDS "deploy v0.1.0"
        │
        ▼
VDS: /srv/tg-bot-eng-teacher
  scripts/deploy.sh
  ├─ git fetch --tags && git checkout v0.1.0
  ├─ sqlite3 .backup  → backups/pre-release-*.db (хранит 10)
  ├─ docker compose build        # сборка на самом VDS
  ├─ docker compose up -d        # alembic upgrade head + запуск
  └─ проверяет, что контейнер Running
```

Почему так:

- **Сборка на VDS** — не нужен registry и GHCR-логин; образ сразу под рукой.
- **Без self-hosted runner** — на публичном репо runner на VDS был бы
  root-эквивалентом для любого, кто получит доступ к workflow.
- **SSH-ключ ограничен forced command** — им нельзя выполнить ничего, кроме
  `scripts/deploy.sh`.
- **Тег обязан быть в `main`** — в `main` уже требуется зелёный CI, поэтому
  релиз не может обогнать тесты.

Миграции применяются автоматически при старте контейнера:
`command: sh -c "alembic upgrade head && exec python -m app"`.

---

## 2. Требования

**На VDS:**

- Linux (проверено на Ubuntu/Debian), root-доступ.
- Docker Engine + Compose v2.24 или новее (`docker compose version`).
- `git`, `sqlite3`.
- Свободно ≥ 2 GB диска под образы и build cache.
- Желательно ≥ 1 GB RAM; при 1 GB **обязателен swap** (см. ниже).

**Локально:**

- `git`, `ssh`, `ssh-keygen`.
- [GitHub CLI](https://cli.github.com/) (`gh`) с доступом к репозиторию.
- Права администратора репозитория (для секретов).

Текущий VDS (на момент написания): 1 vCPU, 978 MiB RAM, swap 0, 5.4 GB free.
Этого достаточно, но swap нужно добавить до первой сборки.

---

## 3. Подготовка VDS

Все команды ниже — **на VDS под root**, если не указано иное.

### 3.1. Swap (обязательно при 1 GB RAM)

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl -w vm.swappiness=10
echo 'vm.swappiness=10' >> /etc/sysctl.conf
free -h
```

Без swap `docker compose build` может быть убит OOM killer.

### 3.2. Пакеты

```bash
apt-get update
apt-get install -y git sqlite3 ca-certificates
docker --version
docker compose version
```

### 3.3. Пользователь `deploy`

Не деплоим от root. Пользователь должен быть в группе `docker`
(это даёт доступ к Docker; см. раздел «Безопасность»).

```bash
useradd --create-home --user-group --shell /bin/bash --groups docker deploy
id deploy
```

### 3.4. Репозиторий

```bash
install -d -m 755 /srv/tg-bot-eng-teacher
chown deploy:deploy /srv/tg-bot-eng-teacher
sudo -u deploy git clone https://github.com/VasilevNStas/tg-bot-eng-teacher.git \
  /srv/tg-bot-eng-teacher
sudo -u deploy git -C /srv/tg-bot-eng-teacher remote -v
```

Репозиторий публичный, поэтому clone идёт по HTTPS без ключей.

### 3.5. Каталог данных

Контейнер запускается от UID/GID пользователя `deploy` (через `BOT_UID` /
`BOT_GID` в `.env`), поэтому `data/` должен принадлежать ему. Так деплой-скрипт
может делать бэкап и восстановление без root.

```bash
install -d -o deploy -g deploy /srv/tg-bot-eng-teacher/data
ls -ld /srv/tg-bot-eng-teacher/data
# ожидаемо: drwxr-xr-x 2 deploy deploy ... data
```

### 3.6. `.env`

```bash
sudo -u deploy cp /srv/tg-bot-eng-teacher/.env.example \
  /srv/tg-bot-eng-teacher/.env
sudo -u deploy chmod 600 /srv/tg-bot-eng-teacher/.env
sudo -u deploy sh -c 'echo "BOT_UID=$(id -u)" >> .env; echo "BOT_GID=$(id -g)" >> .env'
sudo -u deploy vi /srv/tg-bot-eng-teacher/.env   # или любой редактор
```

Минимум, что нужно заполнить:

| Переменная | Значение |
| ---------- | -------- |
| `BOT_TOKEN` | токен **нового** бота от @BotFather |
| `LLM_API_KEY` | ключ OpenRouter |
| `LLM_MODEL_FAST` | например `openai/gpt-4o-mini` |
| `LLM_MODEL_SMART` | например `openai/gpt-4o` |

Проверьте, что `BOT_TOKEN` **не совпадает** с токеном уже запущенного
`tg-bot-ytdl` (иначе Telegram вернёт `409 Conflict`).

Формат токена — `<цифры>:<35 символов>`, без кавычек и пробелов. Безопасная
проверка (токен не печатается):

```bash
sudo -u deploy grep -qE '^BOT_TOKEN=[0-9]+:[A-Za-z0-9_-]{35}$' \
  /srv/tg-bot-eng-teacher/.env \
  && echo "token format ok" || echo "token format INVALID"
```

Остальные переменные можно оставить по умолчанию; `DB_PATH` и `PROMPTS_DIR`
переопределяются в `docker-compose.yml` на `/app/data/bot.db` и `/app/prompts`.

---

## 4. SSH-ключ для деплоя

### 4.1. Генерация (локально)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/tg_bot_eng_teacher_deploy -N "" -C "gha-deploy"
```

Приватный ключ останется на Маке и уйдёт только в GitHub Secrets.
Публичный — на VDS.

### 4.2. Установка публичного ключа (на VDS, root)

Сначала скопируйте публичный ключ на VDS (локально):

```bash
scp ~/.ssh/tg_bot_eng_teacher_deploy.pub root@193.168.49.204:/tmp/
```

Затем на VDS:

```bash
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
printf 'command="bash /srv/tg-bot-eng-teacher/scripts/deploy.sh",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding %s\n' \
  "$(cat /tmp/tg_bot_eng_teacher_deploy.pub)" \
  >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
rm -f /tmp/tg_bot_eng_teacher_deploy.pub
```

`command="..."` — forced command. Что бы ни прислал клиент, выполнится только
`scripts/deploy.sh`, а исходная команда попадёт в `$SSH_ORIGINAL_COMMAND`.
`no-pty`, `no-port-forwarding` и остальное закрывают лишние возможности.

### 4.3. Проверка (локально)

```bash
ssh -i ~/.ssh/tg_bot_eng_teacher_deploy \
  -o IdentitiesOnly=yes deploy@193.168.49.204
```

Ожидаемо:

```
usage: deploy vX.Y.Z (got: '')
```

и код выхода `64`. Это нормально: forced command получил пустую команду и
отказался деплоить. Если видите `Permission denied (publickey)` — проверьте
права `.ssh`, `authorized_keys` и что ключ добавлен именно пользователю
`deploy`.

---

## 5. Секреты GitHub

Выполняется **локально из каталога репозитория**.

```bash
gh auth status
gh secret set VDS_HOST --body "193.168.49.204"
gh secret set VDS_USER --body "deploy"
gh secret set VDS_SSH_KEY < ~/.ssh/tg_bot_eng_teacher_deploy
ssh-keyscan -H 193.168.49.204 | gh secret set VDS_KNOWN_HOSTS
gh secret list
```

Должны появиться 4 секрета: `VDS_HOST`, `VDS_USER`, `VDS_SSH_KEY`,
`VDS_KNOWN_HOSTS`.

Workflow использует `environment: production`. GitHub создаёт окружение
автоматически при первом запуске. Если хотите ручное подтверждение перед
деплоем, создайте его заранее в Settings → Environments → New environment
(`production`) и добавьте Required reviewers.

---

## 6. Первый релиз

Перед тегом убедитесь, что `main` зелёный и содержит нужный код:

```bash
git switch main
git pull --ff-only
gh run list --workflow ci.yml --limit 1
```

Ставим тег и пушим:

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

Смотрим запуск:

```bash
gh run list --workflow release.yml --limit 3
run_id=$(gh run list --workflow release.yml --limit 1 \
  --json databaseId --jq '.[0].databaseId')
gh run watch "$run_id"
```

Что произойдёт на VDS:

1. `git fetch --tags --force && git checkout --force v0.1.0`
2. Бэкап `data/bot.db` → `backups/pre-release-<timestamp>.db`
3. `docker compose build` (первый раз дольше: тянет базовые образы)
4. `docker compose up -d --remove-orphans`
5. Ожидание `Running` до 60 секунд; при падении — логи и exit 1

Первый деплой создаёт БД и применяет миграции автоматически. Контент в БД
**не импортируется** автоматически — это делается отдельно (см. ниже).

### Импорт контента (один раз после первого деплоя)

Deploy-ключ ограничен forced command, поэтому зайти им в shell нельзя.
Импорт выполняйте под root на VDS:

```bash
cd /srv/tg-bot-eng-teacher
sudo -u deploy docker compose run --rm bot \
  python -m app.cli import-content content/topics
sudo -u deploy docker compose restart bot
```

Альтернатива — временно выполнить импорт из workflow/через отдельный ключ.
Импорт идемпотентен: повторный запуск не создаёт дублей.

---

## 7. Проверка после деплоя

На VDS (root):

```bash
cd /srv/tg-bot-eng-teacher
sudo -u deploy docker compose ps
sudo -u deploy docker inspect -f '{{.State.Status}} restarts={{.RestartCount}}' \
  "$(sudo -u deploy docker compose ps -q bot)"
sudo -u deploy docker compose logs --tail=100 bot
ls -lh backups/ | tail -5
```

Ожидаемо:

- контейнер `tg-bot-eng-teacher-bot-1` в статусе `Up`;
- `running restarts=0` (иначе бот падает и перезапускается);
- в логах `bot_started`, нет `401/402/429`, нет traceback;
- в `backups/` появился свежий файл (со второго релиза).

В Telegram:

1. `/start` отвечает.
2. `/words` показывает карточку.
3. `/drill` работает без LLM.
4. Один обычный диалог возвращает ответ.

---

## 8. Следующие релизы

1. Изменения вливаются в `main` только через PR (branch protection).
2. Обновите `CHANGELOG.md` (если ведёте).
3. Поставьте тег:

```bash
git switch main && git pull --ff-only
git tag -a v0.1.1 -m "v0.1.1"
git push origin v0.1.1
```

Повторный деплой **без нового тега** (например, после сбоя сети):

```bash
gh workflow run release.yml -f tag=v0.1.1
```

Тег можно удалить и переставить, но помните: деплой всегда проверяет, что тег
является предком `main`.

---

## 9. Ручной деплой

Если GitHub Actions недоступен, на VDS под root:

```bash
cd /srv/tg-bot-eng-teacher
sudo -u deploy git fetch --tags --force
sudo -u deploy git checkout --force v0.1.1
sudo -u deploy docker compose build
sudo -u deploy docker compose up -d --remove-orphans
sudo -u deploy docker compose ps
```

Бэкап перед ручным деплоем:

```bash
sudo -u deploy sqlite3 data/bot.db \
  ".backup 'backups/manual-$(date +%Y%m%d-%H%M%S).db'"
```

---

## 10. Откат

### Откат приложения (без изменения схемы)

Разверните предыдущий тег:

```bash
gh workflow run release.yml -f tag=v0.1.0
```

Или вручную по разделу 9.

### Откат миграции

Автоматического downgrade нет. Порядок:

1. Остановить бота: `sudo -u deploy docker compose stop bot`
2. Если миграция обратима: `sudo -u deploy docker compose run --rm bot alembic downgrade -1`
3. Иначе — восстановить БД из бэкапа (раздел 11)
4. Запустить предыдущий тег
5. Зафиксировать инцидент в `.opencode/WORK_LOG.md`

---

## 11. Бэкапы и восстановление

Деплой-скрипт делает бэкап автоматически и хранит последние **10** файлов:

```
/srv/tg-bot-eng-teacher/backups/pre-release-YYYYMMDD-HHMMSS.db
```

Ручной бэкап (SQLite `.backup` безопасен на работающей БД):

```bash
cd /srv/tg-bot-eng-teacher
sudo -u deploy mkdir -p backups
sudo -u deploy sqlite3 data/bot.db \
  ".backup 'backups/manual-$(date +%Y%m%d-%H%M%S).db'"
```

Восстановление:

```bash
cd /srv/tg-bot-eng-teacher
sudo -u deploy docker compose stop bot
sudo -u deploy cp backups/pre-release-20261029-120000.db data/bot.db
sudo -u deploy rm -f data/bot.db-wal data/bot.db-shm
sudo -u deploy docker compose up -d
```

После восстановления проверьте `/start`, `/drill`, `/words`.

Рекомендуется дополнительно вывозить бэкапы с VDS (R2/S3) — это отдельная
задача (B-025).

---

## 12. Диагностика

| Симптом | Причина | Решение |
| ------- | ------- | ------- |
| `Permission denied (publickey)` | ключ не в `authorized_keys` или неверные права | `chmod 700 ~deploy/.ssh`, `chmod 600 authorized_keys`, владелец `deploy` |
| `usage: deploy vX.Y.Z` при ручном ssh | forced command, пустая команда | это ожидаемо; деплой запускается из Actions |
| `invalid release tag` | тег не `vX.Y.Z` | пересоздайте тег по SemVer |
| `tag vX is not on main` | тег на непроверенном коммите | влейте PR в `main`, поставьте тег заново |
| `missing /srv/.../.env` | нет `.env` | создайте по разделу 3.6 |
| `unable to open database file` | `data/` не принадлежит пользователю контейнера | `chown -R deploy:deploy data`; проверьте `BOT_UID`/`BOT_GID` в `.env` |
| `env file ... not found` | нет `.env` | создать `.env` или обновить Compose |
| `env_file.required` unknown | Compose < 2.24 | обновите Compose или замените на `env_file: .env` |
| Контейнер перезапускается | неверный `.env` / ошибка валидации | `docker compose logs --tail=200 bot` |
| `TokenValidationError: Token is invalid!` | пустой/неверный `BOT_TOKEN`, кавычки/пробелы, токен отозван | проверьте формат (3.6), перевыпустите в @BotFather, затем `up -d --force-recreate` |
| Изменения `.env` не применились | `docker compose restart` не пересоздаёт контейнер | `docker compose up -d --force-recreate` |
| `409 Conflict` в Telegram | тот же `BOT_TOKEN` запущен дважды | остановите второй процесс/старый systemd-юнит |
| OOM kill при сборке | мало RAM, нет swap | добавьте swap (3.1), повторите |
| `No space left on device` | образы/build cache | `docker system prune -af`, удалите старые бэкапы |
| Деплой прошёл, бот молчит | контейнер упал после старта | `docker compose ps -a`, логи, проверьте `BOT_TOKEN`/`LLM_API_KEY` |

Полезные команды:

```bash
cd /srv/tg-bot-eng-teacher
sudo -u deploy docker compose ps -a
sudo -u deploy docker compose logs -f --tail=200 bot
sudo -u deploy docker inspect --format '{{.State.Status}}' \
  "$(sudo -u deploy docker compose ps -q bot)"
df -h /; free -h; docker system df
```

---

## 13. Безопасность

- **Deploy-ключ** ограничен forced command и не имеет pty/forwarding. Хранится
  только в GitHub Secrets; при компрометации — удалить строку из
  `authorized_keys` и перевыпустить ключ/секрет.
- **Пользователь `deploy` в группе `docker`** технически эквивалентен root на
  хосте. Это осознанный компромисс для pet-проекта. Не давайте этому
  пользователю пароль и не открывайте ему shell по паролю.
- **`.env`** — `chmod 600`, не коммитится, не попадает в образ (`.dockerignore`).
- **Контейнер** — non-root UID 10001, `no-new-privileges`, лимиты RAM/CPU.
- **Секреты не печатать** в логи; в workflow ключ пишется в файл с `chmod 600`.
- **Публичный репозиторий**: workflow `release` запускается только по тегу или
  вручную, не по `pull_request`.
- **Branch protection**: в `main` нельзя пушить напрямую; требуется PR и
  зелёный `ci / test`.

---

## 14. Чек-лист

Одноразовая настройка:

- [ ] swap добавлен (`free -h` показывает 2G)
- [ ] установлены `git`, `sqlite3`, Docker Compose ≥ 2.24
- [ ] создан пользователь `deploy`, добавлен в группу `docker`
- [ ] репозиторий склонирован в `/srv/tg-bot-eng-teacher`
- [ ] `data/` принадлежит `deploy`, `BOT_UID`/`BOT_GID` записаны в `.env`
- [ ] `.env` создан, заполнен, `chmod 600`, токен отличается от других ботов
- [ ] публичный deploy-ключ добавлен с forced command
- [ ] `ssh deploy@HOST` возвращает `usage: deploy vX.Y.Z`
- [ ] 4 секрета добавлены в GitHub (`gh secret list`)
- [ ] окружение `production` создано (опционально — reviewers)

Каждый релиз:

- [ ] PR влит в `main`, CI зелёный
- [ ] тег `vX.Y.Z` поставлен и запушен
- [ ] workflow `release` завершился успешно
- [ ] контейнер `Up`, в логах `bot_started`
- [ ] `/start`, `/drill`, `/words` работают
- [ ] свежий бэкап появился в `backups/`
