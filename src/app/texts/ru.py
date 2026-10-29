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

LLM_TEMPORARY_ERROR = "Сервис сейчас перегружен. Попробуй ещё раз через минуту."

LLM_INVALID_RESPONSE = "Не получилось разобрать ответ. Попробуй переформулировать."

LLM_UNAVAILABLE = "Сервис временно недоступен. Попробуй позже."

BUDGET_EXHAUSTED = (
    "На сегодня лимит практики исчерпан. Прогресс сохранён — возвращайся завтра, продолжим."
)

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

HELP = (
    "<b>Команды</b>\n"
    "/start — начать заново\n"
    "/correction &lt;текст&gt; — полный разбор ошибок\n"
    "/drill — тренировка неправильных глаголов\n"
    "/vocab — сохранённые слова\n"
    "/review — повторение слов\n"
    "/stats — прогресс и слабые темы\n"
    "/roleplay — ролевая сцена\n"
    "/stop — завершить ролевую сцену\n"
    "/timezone — часовой пояс\n"
    "/quiet — тихие часы\n"
    "/cancel — завершить тренировку\n"
    "/help — эта справка"
)
