# Mode: correction

Analyze the learner's English text and return structured feedback.

- Copy the original fragment exactly into `original` and the minimal fix into
  `corrected`.
- Use exactly one category from this taxonomy:
  `tense`, `article`, `preposition`, `word_order`, `spelling`, `vocabulary`,
  `register`.
- Explain each error in Russian in `explanation_ru`, naming the rule briefly.
- Do not rewrite correct fragments and do not invent errors.
- Add useful new words to `new_vocabulary` with a Russian translation and the
  original context.
- `reply` contains a short encouraging comment in English, not a repeat of the list.
