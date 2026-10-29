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
    "/drill — тренировка неправильных глаголов\n"
    "/cancel — завершить тренировку\n"
    "/help — эта справка"
)
