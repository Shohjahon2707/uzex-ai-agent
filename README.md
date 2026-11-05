# Birja AI — агент по реестру сделок биржи (RU + UZ)

Веб-чат на Django. Агент (LLM) не отвечает «из головы»: он пишет SQL к базе реестра, получает строки и отвечает
только по ним. Нет строк — честно говорит, что данных нет. Под каждым ответом виден SQL и результат.

Конкретные требования отбора и способы проверки собраны в [чек-листе приёмки](docs/ACCEPTANCE.md).
Чат поддерживает RU/UZ-интерфейс, выделение результатов, списки и таблицы; SQL раскрывается отдельно
в разделе «Данные и расчёт». Количество записей и период на странице берутся из базы, а не из макета.

## Запуск у себя

```bash
python -m venv venv
venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt

# 1) положи все CSV в папку data/ и собери базу (~1 минута)
python scripts/build_db.py --data data --out data/birja.db
#    внимательно прочитай «ОТЧЁТ-ПРОВЕРКА» в конце: количества по годам должны совпасть с README

# 2) ключ DeepSeek (ввод скрыт, настройки сохраняются в .env)
python manage.py configure_deepseek

# 3) запуск
python manage.py check_llm    # проверка ключа, модели и вызова инструментов
python manage.py runserver
# открыть http://127.0.0.1:8000

# 4) прогон своих вопросов с эталоном
python scripts/golden.py scripts/golden.csv http://127.0.0.1:8000
```

## Если чат не отвечает

Настройки читаются из `.env` рядом с `manage.py`, а не из `.env.example`.
После правки `.env` останови сервер (Ctrl+C) и снова запусти `python manage.py runserver`.
Уже заданные переменные окружения имеют приоритет над `.env`.

По умолчанию пример настроек использует DeepSeek. Для переключения существующего проекта:

```bash
python manage.py configure_deepseek
python manage.py check_llm
```

Первая команда запрашивает ключ скрыто и сохраняет только в локальный `.env`:
`LLM_PROVIDER=deepseek`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL=deepseek-v4-pro` и
`DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`. Остальные настройки сохраняются.
Перезапусти работающий Django после изменения файла. Идентификатор модели взят из настроек пользователя;
доступность ключа и этой модели у провайдера подтверждает только живой `check_llm`.
Список моделей: `python manage.py check_llm --list-models`.

И чат, и проверка подключения используют `tool_choice=auto`: thinking-режим DeepSeek не принимает
принудительный `required`. Поле протокола `reasoning_content`, если оно возвращено с вызовом
инструмента, передаётся обратно API без вывода в интерфейс или логи.

В профиле DeepSeek старые `LLM_API_KEY_1…5`, `LLM_BASE_URL` и `LLM_MODEL` Groq не используются.
Для возврата к прежнему профилю задай `LLM_PROVIDER=openai-compatible`.

Для Groq используются `LLM_API_KEY`, `LLM_BASE_URL=https://api.groq.com/openai/v1`
и `LLM_MODEL=openai/gpt-oss-20b` (если модель доступна в аккаунте).
Также поддерживается `GROQ_API_KEY`, когда `LLM_API_KEY` отсутствует.

```bash
python manage.py check_llm                # проверяет API и обмен инструментами без базы данных
python manage.py check_llm --list-models  # выводит реальные идентификаторы моделей провайдера
```

`401` — провайдер не принимает ключ; `403` — запрещён доступ; `404` — неверный адрес или
недоступная модель; `429` — квота/частота запросов; `5xx` — сбой провайдера.
Проверка делает реальные API-запросы и расходует квоту. При ошибке команда завершается с ненулевым кодом.
Ключи в выводе скрыты. Чат показывает понятную причину, а при `DEBUG=1` — раскрываемые
очищенные детали. В режиме `DEBUG=0` детали остаются в логе сервера.
Ошибки API не добавляются в историю диалога.

Не коммить `.env`. В `.env.example` должен оставаться плейсхолдер, а не настоящий ключ.

### Круговое переключение пяти ключей

В локальном `.env` можно заменить одиночную строку `LLM_API_KEY` на:

```dotenv
LLM_PROVIDER=openai-compatible
LLM_API_KEY_1=your_first_key
LLM_API_KEY_2=your_second_key
LLM_API_KEY_3=your_third_key
LLM_API_KEY_4=your_fourth_key
LLM_API_KEY_5=your_fifth_key
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=openai/gpt-oss-20b
```

Впиши настоящие ключи только в `.env` и перезапусти Django. Нумерованные ключи имеют приоритет
над одиночными `LLM_API_KEY` / `GROQ_API_KEY`; одинаковые значения удаляются из списка.
Для каждого вопроса выбирается следующий ключ: 1 → 2 → 3 → 4 → 5 → 1.
Все API-вызовы внутри одного ответа используют один выбранный ключ. В терминале показано только имя переменной.

```bash
python manage.py check_llm --show-rotation # порядок без API-запросов
python manage.py check_llm --key 3         # реальная проверка выбранного ключа
```

Квоты Groq обычно действуют на организацию и модель, а не на отдельный ключ. Ротация ключей одного
аккаунта не создаёт отдельные лимиты. На 429 агент не перебирает остальные ключи внутри того же вопроса;
тестовый прогон останавливается с `BLOCKED` и кодом 2, чтобы не выдавать квоту за 25 неверных ответов.
Круговой счётчик находится в памяти процесса и сбрасывается при перезапуске. У нескольких workers
отдельные счётчики; строгая последовательность для этой проверки рассчитана на один процесс Django.
Ключи должны относиться к одному провайдеру с общими `LLM_BASE_URL` и `LLM_MODEL`.

Проверки кода без платных API-запросов: `python manage.py test agent.tests`.
Они включают настоящий OpenAI SDK с локальным тестовым HTTP-сервером и SQLite.
Такие проверки не подтверждают доступность выбранной модели у внешнего провайдера:
для этого нужен `check_llm` с действующим ключом на машине, где запускается сервис.

Независимая проверка формул по исходным CSV:

```bash
python scripts/verify_data.py --data data --db data/birja.db
```

Проверяются годовые количества, статусы, экспорт, объёмы по единицам, брокеры на обеих сторонах
и точный пересчёт USD в сумы. Команда не вызывает модель и завершается с ошибкой при расхождении.
В SQL-инструменте доступны `sum_money(amount)` для одной валюты и
`sum_uzs(amount, usd_rate, is_usd)` для суммы в сумах. Агрегаты используют Decimal и возвращают
точную десятичную строку. Для сортировки таких сумм применяется `COLLATE decimal`.
Они регистрируются при открытии базы; уже собранная база поддерживается.

SQL-инструмент автоматически добавляет `COLLATE decimal` к выражениям с точными денежными
агрегатами, чтобы `ORDER BY`/`LIMIT` выбирали числовой топ, а не лексический порядок строк.
В основаниях ответа показан фактически выполненный SQL с этой поправкой.
Самостоятельный вопрос начинается без истории старых фильтров. Для явных продолжений вроде
«а за 2024 год?» или «сколько из них…» передаётся контекст от последнего самостоятельного вопроса,
без более старых условий. Если исходный вопрос недоступен, агент просит уточнить условия вместо
подсчёта по всей базе. Браузер сохраняет до 20 последних сообщений для передачи контекста.

## Деплой на сервер (Ubuntu, 512 МБ)

CSV и сборку базы на сервере НЕ делаем — собранный `birja.db` копируем целиком.

```bash
# на компьютере
gzip -k data/birja.db
scp data/birja.db.gz root@IP:/opt/birja.db.gz

# на сервере (после удаления CS-сервера)
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
apt update && apt install -y python3-venv git
git clone <твой-приватный-репозиторий> /opt/birja && cd /opt/birja
mkdir -p data && gunzip -c /opt/birja.db.gz > data/birja.db
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt gunicorn
cp .env.example .env && nano .env      # LLM_API_KEY, DEBUG=0, DJANGO_SECRET_KEY
ufw allow 8000 || true
./venv/bin/gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --timeout 120
```

Чтобы работало после выхода из SSH и перезагрузки — systemd-сервис `/etc/systemd/system/birja.service`:

```ini
[Unit]
Description=Birja AI
After=network.target

[Service]
WorkingDirectory=/opt/birja
ExecStart=/opt/birja/venv/bin/gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

`systemctl daemon-reload && systemctl enable --now birja` — и проверь `http://IP:8000/api/health`.

## Устройство

- `scripts/build_db.py` — CSV → SQLite, чистка чисел/дат, view `deals_v` (volume, unit_price, amount_uzs, amount_usd …).
- `agent/tools.py` — `run_sql` (только SELECT, база открыта read-only, таймаут, лимит строк) и `lookup`
  (поиск точных названий: регистр, ё/е, апострофы, опечатки Samarkand~Samarqand).
- `agent/prompts.py` — правила ответа и схема базы (подставляется из самой базы).
- `agent/service.py` — цикл LLM ↔ инструменты, любой OpenAI-совместимый API.
- `templates/chat.html` — чат без регистрации и установки.
