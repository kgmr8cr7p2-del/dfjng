# dfjng

## YouTube автозагрузка

Добавьте данные OAuth в `config.json` → `youtube` и включите `enabled: true`.

Минимально нужны:

- `client_id`
- `client_secret`
- `refresh_token`

После успешной загрузки бот отправит ссылку вида `https://youtu.be/<id>`.

### Расписание публикаций (Shorts)

В GUI укажите:

- время первого видео (`HH:MM`)
- периодичность в минутах
- сколько видео загрузить

Эти значения сохраняются в `config.json` → `youtube.schedule`.

### Shorts режим

`append_shorts_tag: true` добавляет `#shorts` в описание, а также в заголовок и теги, чтобы ролики обрабатывались как Shorts.

### Режим описания (80/20)

`youtube.prompt_mode` позволяет чередовать описание:

- `full_prompt_ratio: 0.2` — доля роликов с полным промптом (20%)
- остальные 80% получают короткий prompt-summary
- `summary_max_chars` — длина краткого описания
- `static_description` — статическое описание, добавляется ко всем роликам
