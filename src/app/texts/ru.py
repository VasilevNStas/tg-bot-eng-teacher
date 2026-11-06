"""Russian UI strings (single source of truth for user-facing text)."""

START = (
    "<b>Привет!</b> Я помогу тебе практиковать английский.\n\n"
    "Просто напиши сообщение по-английски — я отвечу и мягко исправлю ошибки. "
    "Или запусти тренировку неправильных глаголов: /drill.\n\n"
    "Подробнее — /help."
)

ONBOARDING_INTRO = (
    "<b>Давай познакомимся.</b>\n\n"
    "Чтобы подобрать уровень, пройди короткий тест (6 вопросов) "
    "или выбери уровень вручную."
)

ONBOARDING_REQUIRED = "Сначала выбери уровень — это займёт минуту. Потом продолжим практику."

PLACEMENT_QUESTION = "Вопрос {number} из {total}\n\n{prompt}"

PLACEMENT_RESULT = (
    "Готово! Твой уровень — <b>{level}</b>.\n\nНапиши сообщение, чтобы начать практику."
)

CHOOSE_LEVEL = "Выбери свой уровень:"

LEVEL_SET = "Уровень сохранён: <b>{level}</b>.\n\nНапиши сообщение, чтобы начать практику."

STALE_QUESTION = "Этот вопрос уже неактуален."
UNKNOWN_LEVEL = "Неизвестный уровень."

BUTTON_TAKE_TEST = "Пройти тест"
BUTTON_SKIP = "Пропустить"
BUTTON_CHANGE_LEVEL = "Изменить уровень"

MESSAGE_TOO_LONG = "Сообщение слишком длинное. Сократи его и попробуй снова."

LLM_TEMPORARY_ERROR = (
    "Сервис сейчас перегружен. Попробуй ещё раз через минуту или потренируйся: /drill, /words."
)

LLM_INVALID_RESPONSE = "Не получилось разобрать ответ. Попробуй переформулировать."

LLM_UNAVAILABLE = "Сервис временно недоступен. Попробуй позже или потренируйся: /drill, /words."

BUDGET_EXHAUSTED = (
    "На сегодня лимит практики исчерпан. Прогресс сохранён — возвращайся завтра, продолжим."
)

SERVICE_BUDGET_EXHAUSTED = "Сервис временно недоступен из-за общего лимита. Попробуй позже."

BUDGET_ALERT = "⚠️ LLM-бюджет: {percent}% — ${spent:.2f} из ${budget:.2f}."

CONVERSATION_LIMIT_REACHED = "Диалог достиг лимита. Следующее сообщение начнёт новый диалог."

DRILL_QUESTION = (
    "<b>Вопрос {number} из {total}</b>\n\n"
    "<b>{base}</b>{ipa}\n"
    "{translation}\n\n"
    "Напиши форму: <b>{form}</b>"
)

DRILL_FORM_PAST = "Past Simple (V2)"
DRILL_FORM_PARTICIPLE = "Past Participle (V3)"

CORRECTION_USAGE = "Отправь текст для разбора:\n<code>/correction I go to school yesterday</code>"
CORRECTION_HEADER = "<b>Разбор</b>"
CORRECTION_NONE = "Ошибок не найдено."
NEW_VOCABULARY_HEADER = "<b>Новые слова</b>"

ERROR_CATEGORY_RU: dict[str, str] = {
    "tense": "Время",
    "article": "Артикль",
    "preposition": "Предлог",
    "word_order": "Порядок слов",
    "spelling": "Орфография",
    "vocabulary": "Лексика",
    "register": "Регистр",
}

VOCAB_EMPTY = "Пока нет сохранённых слов. Они появятся после диалога."
VOCAB_HEADER = "<b>Сохранённые слова</b> ({count})"
VOCAB_ITEM = "{number}. <b>{term}</b> — {translation}"
VOCAB_CONTEXT = "<i>{context}</i>"

REVIEW_HEADER = "<b>Повторение</b>"
REVIEW_PROMPT = "Насколько легко вспомнил слово?"
REVIEW_EMPTY = "Нет слов к повторению. Новые слова сохраняются автоматически после диалога."
REVIEW_DONE = "Готово! Слов к повторению пока нет."
REVIEW_UNKNOWN = "Эта карточка уже недоступна."
BUTTON_AGAIN = "Ещё раз"
BUTTON_GOOD = "Хорошо"
BUTTON_EASY = "Легко"

TIMEZONE_CURRENT = (
    "Часовой пояс: <b>{timezone}</b>\n"
    "Изменить: <code>/timezone Europe/Moscow</code>\n"
    "Примеры: Europe/Moscow, Asia/Almaty, Europe/Berlin."
)
TIMEZONE_SET = "Часовой пояс сохранён: <b>{timezone}</b>."
TIMEZONE_INVALID = "Неизвестный часовой пояс. Примеры: Europe/Moscow, Asia/Almaty, Europe/Berlin."
QUIET_CURRENT_ON = (
    "Тихие часы: <b>{start}–{end}</b> ({timezone}).\nНапоминания в это время откладываются."
)
QUIET_CURRENT_OFF = (
    "Тихие часы выключены.\n"
    "Установить: <code>/quiet 22:00-08:00</code>; выключить: <code>/quiet off</code>."
)
QUIET_SET = "Тихие часы сохранены: <b>{start}–{end}</b> ({timezone})."
QUIET_OFF = "Тихие часы выключены."
QUIET_INVALID = "Формат: <code>/quiet 22:00-08:00</code> или <code>/quiet off</code>."

ROLEPLAY_CHOOSE = "<b>Ролевые сценарии</b>\n\nВыбери сцену:"
ROLEPLAY_STARTED = (
    "<b>Сцена: {title}</b>\n\nПиши по-английски — я останусь в роли. Чтобы выйти, отправь /stop."
)
ROLEPLAY_STOPPED = "Ролевая сцена завершена."
ROLEPLAY_NONE = "Сейчас нет активной ролевой сцены."
ROLEPLAY_UNKNOWN = "Сцена не найдена. Открой список: /roleplay."
ROLEPLAY_UNAVAILABLE = "Сценарии пока недоступны."

NEW_CONVERSATION = "Готово. Новый диалог начнётся со следующего сообщения."
NEW_NOTHING = "Сейчас нет активного диалога. Просто напиши сообщение."

DELETE_WARNING = (
    "<b>Удалить все данные?</b>\n\n"
    "Будут удалены история диалогов, исправления, словарь, SRS, прогресс, "
    "попытки и напоминания. Действие необратимо."
)
BUTTON_DELETE = "Удалить всё"
BUTTON_DELETE_CANCEL = "Отмена"
DELETE_CANCELLED = "Удаление отменено."
DELETE_NOTHING = "Данных для удаления нет."
DELETE_ALREADY = "Данные уже удалены."
DELETE_DONE = (
    "<b>Готово.</b>\n\n"
    "Удалено диалогов: {conversations}, сообщений: {messages}, слов: {vocabulary}.\n"
    "Профиль и настройки тоже удалены. /start создаст новый."
)

REMINDER_SRS = "Пора повторить слова: /review"
REMINDER_DAILY = "Пора немного попрактиковаться: /review или /drill"

STATS_HEADER = "<b>Статистика</b>"
STATS_PRACTICE_HEADER = "<b>Практика</b>"
STATS_ATTEMPTS = "Попыток: {total}, верно: {correct} ({accuracy}%)"
STATS_BREAKDOWN = "Почти: {partial}, ошибок: {incorrect}, сессий: {sessions}"
STATS_NO_PRACTICE = "Пока нет попыток в /drill."
STATS_STREAK = "Лучшая серия: {streak}"
STATS_WEAK_TOPICS_HEADER = "<b>Слабые темы</b>"
STATS_TOPIC_ITEM = "{title} — {accuracy}% ({attempts} попыток)"
STATS_NO_TOPICS = "Пока нет данных по темам."
STATS_VOCAB_HEADER = "<b>Слова</b>"
STATS_VOCAB_TOTAL = "Сохранено: {total}, к повторению: {due}"
STATS_WEAK_WORDS_HEADER = "<b>Слабые слова</b>"
STATS_WEAK_WORD_ITEM = "{term} — {lapses} провалов"
STATS_NO_WEAK_WORDS = "Слабых слов пока нет."
STATS_WORDS_HEADER = "<b>Карточки слов</b>"
STATS_WORDS_SUMMARY = "Карточек: {sessions}, попыток: {attempts}, верно: {correct} ({accuracy}%)"
STATS_WORDS_NO_DATA = "Пока нет завершённых карточек /words."
STATS_WORDS_SEEN = "Слов из банка в работе: {seen}"
STATS_WEAK_BANK_WORDS_HEADER = "<b>Слабые слова из карточек</b>"
STATS_WEAK_BANK_WORD_ITEM = "{term} — {wrong} из {attempts}"
STATS_NO_WEAK_BANK_WORDS = "Слабых слов из карточек пока нет."

STATS_ERRORS_HEADER = "<b>Ошибки в разборе</b>"
STATS_ERROR_ITEM = "{category}: {count}"
STATS_NO_ERRORS = "Ошибок в разборе пока нет."

DRILL_CORRECT = "✅ Верно!"
DRILL_PARTIAL = "Почти! Похоже на опечатку."
DRILL_INCORRECT = "❌ Неверно."

DRILL_ANSWER = "Твой ответ: <b>{answer}</b>"
DRILL_EXPECTED = "Правильно: <b>{expected}</b>"

DRILL_DETAILS = (
    "<b>{base}</b>{ipa} — {translation}\nPast Simple: {past}\nPast Participle: {participle}"
)

DRILL_SUMMARY = (
    "<b>Тренировка завершена.</b>\n\n"
    "Верно: <b>{correct}</b> из {total}\n"
    "Почти: {partial}\n\n"
    "Прогресс по теме сохранён."
)

DRILL_CANCELLED = "Тренировка завершена досрочно."

DRILL_CANCELLED_STATS = "Тренировка завершена досрочно.\n\nВерно: <b>{correct}</b> из {total}"

DRILL_NOTHING_TO_CANCEL = "Сейчас нет активной тренировки."
DRILL_UNAVAILABLE = "Банк упражнений пуст. Попроси администратора импортировать контент."
DRILL_SESSION_ERROR = "Сессия тренировки потеряна. Начни заново: /drill."

WORDS_UNAVAILABLE = "Банк слов пуст. Попроси администратора импортировать контент."
WORDS_HEADER = "<b>Карточка слов</b> ({count})"
WORDS_STUDY_ITEM = "{number}. <b>{term}</b>{ipa} — {translation}"
WORDS_STUDY_FOOTER = "Запомни слова и начни квиз."
WORDS_QUESTION = "<b>Вопрос {number} из {total}</b>\n\n{prompt}\n\n{direction}"
WORDS_DIRECTION_EN_RU = "Напиши перевод на русский."
WORDS_DIRECTION_RU_EN = "Напиши слово по-английски."
WORDS_CORRECT = "✅ Верно!"
WORDS_PARTIAL = "Почти! Похоже на опечатку."
WORDS_INCORRECT = "❌ Неверно."
WORDS_ANSWER = "Твой ответ: <b>{answer}</b>"
WORDS_EXPECTED = "Правильно: <b>{expected}</b>"
WORDS_SUMMARY = (
    "<b>Карточка завершена.</b>\n\n"
    "Верно: <b>{correct}</b> из {total}\n"
    "Почти: {partial}, ошибок: {incorrect}"
)
WORDS_CANCELLED = "Карточка завершена досрочно."
WORDS_CANCELLED_STATS = "Карточка завершена досрочно.\n\nВерно: <b>{correct}</b> из {total}"
WORDS_NOTHING_TO_CANCEL = "Сейчас нет активной карточки."
WORDS_SESSION_ERROR = "Сессия карточки потеряна. Начни заново: /words."
WORDS_SETTINGS = "<b>Настройки карточек</b>\n\nСлов: <b>{size}</b>\nНаправление: <b>{direction}</b>"
WORDS_NO_ERRORS_TO_RETRY = "Ошибок нет — можно начать новую карточку."
WORDS_DIRECTION_LABELS: dict[str, str] = {
    "en_ru": "English → русский",
    "ru_en": "русский → English",
    "both": "в обе стороны",
    "mix": "смешанное",
}
BUTTON_START_QUIZ = "Начать квиз"
BUTTON_SETTINGS = "Настройки"
BUTTON_RETRY_WRONG = "Повторить ошибки"
BUTTON_NEW_CARD = "Новая карточка"
BUTTON_CANCEL_TASK = "Выйти из задания"

MENU_TITLE = "<b>Меню</b>\n\nВыбери раздел:"
MENU_LEARN = "Учёба"
MENU_PRACTICE = "Практика"
MENU_PROGRESS = "Прогресс"
MENU_SETTINGS = "Настройки"
MENU_HELP = "Помощь"
MENU_BACK = "Назад"
MENU_LEARN_TEXT = "<b>Учёба</b>\n\nВыбери занятие:"
MENU_PRACTICE_TEXT = "<b>Практика</b>\n\nВыбери формат:"
MENU_PROGRESS_TEXT = "<b>Прогресс</b>\n\nЧто посмотреть?"
MENU_SETTINGS_TEXT = "<b>Настройки</b>\n\nЧто настроить?"
MENU_HELP_TEXT = "Открой справку по всем командам."
MENU_DRILL = "Неправильные глаголы"
MENU_WORDS = "Карточки слов"
MENU_REVIEW = "Повторить слова"
MENU_VOCAB = "Сохранённые слова"
MENU_CORRECTION = "Разбор ошибок"
MENU_ROLEPLAY = "Ролевая сцена"
MENU_STOP = "Завершить сцену"
MENU_NEW = "Новый диалог"
MENU_STATS = "Статистика"
MENU_TIMEZONE = "Часовой пояс"
MENU_QUIET = "Тихие часы"
MENU_ABOUT = "Мои данные"
MENU_DELETE = "Удалить данные"
TASK_NOTHING_TO_CANCEL = "Сейчас нет активного задания."

PRIVACY_NOTICE = (
    "Сообщения обрабатываются внешним LLM-провайдером; полной конфиденциальности "
    "нет. Не отправляй пароли, финансовые, медицинские и юридические данные."
)

ABOUT_ME = (
    "<b>Что бот знает о тебе</b>\n\n"
    "<b>Профиль</b>\n"
    "Telegram ID: <code>{tg_id}</code>\n"
    "Username: {username}\n"
    "Уровень: <b>{level}</b>\n"
    "Часовой пояс: {timezone}\n"
    "Тихие часы: {quiet}\n"
    "В боте с: {created}\n"
    "Последняя активность: {last_seen}\n\n"
    "<b>Настройки карточек</b>\n"
    "Слов в карточке: {card_size}\n"
    "Направление: {direction}\n\n"
    "<b>Что хранится в базе</b>\n"
    "Диалоги: {conversations}\n"
    "Сообщения: {messages}\n"
    "Исправления: {corrections}\n"
    "Сохранённые слова: {vocabulary}\n"
    "Тренировки: {sessions}\n"
    "Ответы в упражнениях: {attempts}\n"
    "Слова с прогрессом: {word_progress}\n"
    "Напоминания: {reminders}\n"
    "Обращения к LLM: {usage}\n\n"
    "<b>Хранение</b>\n"
    "{retention}\n\n"
    "<b>Приватность</b>\n" + PRIVACY_NOTICE + "\n\nПолное удаление: /delete_me"
)
ABOUT_ME_UNKNOWN = "Профиль ещё не создан. Отправь /start."
ABOUT_ME_RETENTION = "История чата хранится до {days} дней."
ABOUT_ME_NO_RETENTION = "История чата не хранится."
QUIET_NONE = "выключены"

HELP = (
    "<b>Справка</b>\n\n"
    "<b>Учёба</b>\n"
    "/drill — неправильные глаголы\n"
    "/words — карточки слов\n"
    "/review — повторение слов\n"
    "/vocab — сохранённые слова\n\n"
    "<b>Практика</b>\n"
    "/correction &lt;текст&gt; — разбор ошибок\n"
    "/roleplay — ролевая сцена\n"
    "/stop — завершить сцену\n"
    "/new — начать новый диалог\n\n"
    "<b>Прогресс</b>\n"
    "/stats — прогресс и слабые темы\n\n"
    "<b>Настройки</b>\n"
    "/timezone — часовой пояс\n"
    "/quiet — тихие часы\n"
    "/about_me — какие данные хранятся\n"
    "/delete_me — удалить мои данные\n\n"
    "<b>Задания и меню</b>\n"
    "/menu — разделы и быстрый доступ\n"
    "/cancel — прервать текущее задание\n"
    "/help — эта справка\n\n"
    "<b>Приватность</b>\n" + PRIVACY_NOTICE
)

RESTRICTED_NEUTRAL = "Сейчас не могу ответить. Попробуйте позже."
RESTRICTED_CONFUSED = "Не понял. Доступно: /start, /help."
RESTRICTED_EXPLICIT = "Доступ ограничен."
RATE_LIMITED = "Слишком часто. Попробуйте через минуту."
