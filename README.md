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
