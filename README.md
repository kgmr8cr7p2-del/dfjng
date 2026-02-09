# dfjng

## YouTube автозагрузка

Добавьте данные OAuth в `config.json` → `youtube` и включите `enabled: true`.

Минимально нужны:

- `client_id`
- `client_secret`
- `refresh_token`

После успешной загрузки бот отправит ссылку вида `https://youtu.be/<id>`.

## Полный шаблон config.json

Готовый шаблон настроек лежит в `config.template.json`. Скопируйте его в `config.json` и заполните нужные значения.
