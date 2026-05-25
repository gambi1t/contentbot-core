# RFC: ContenCore V2
## Multi-tenant платформа с общей PostgreSQL БД, микросервисами и Telegram Mini Apps

## Статус

Draft

## Дата

2026-05-25

## Назначение документа

Этот RFC фиксирует целевую архитектуру ContenCore V2 как платформы для
обслуживания множества клиентов в одной системе.

Документ нужен, чтобы зафиксировать:

- какие технические решения принимаются;
- какие решения считаются обязательными;
- какие принципы нельзя нарушать при развитии системы;
- по каким правилам должен подключаться новый клиент;
- как должны быть устроены данные, сервисы, боты, mini app и управление
  функциями.

## Главный принцип

**Если для подключения нового клиента нужно менять Python-код, целевая
архитектура ещё не достигнута.**

Из этого принципа следуют обязательные ограничения:

- новый клиент создаётся через данные и настройки;
- новый клиент не добавляется через ветку `if client == ...`;
- модули включаются и выключаются через админский интерфейс;
- токены, права, роли, настройки, лимиты и интеграции живут в системных данных,
  а не в разрозненном runtime-коде;
- rollout новых функций должен быть централизованным.

## Проблема текущего состояния

Текущий проект вырос как интеграционный Telegram-бот для контент-продакшна,
публикации и AI-медиа-автоматизации. Это рабочая модель для MVP и ручного
операционного режима, но она плохо подходит для развития в продуктовую
платформу.

Основные проблемы текущего состояния:

- слишком большой объём orchestration и бизнес-логики сосредоточен в одном
  runtime;
- tenant-логика недостаточно формализована;
- данные распределены между кодом, env, Notion, SQLite и JSON state;
- отсутствует одна системная БД как основной источник правды;
- отсутствует нормальный feature management по клиентам;
- отсутствует полноценный административный интерфейс для управления
  клиентами;
- отсутствует строгая модель подключения нового клиента без изменения кода;
- Telegram-бот перегружен функциями, которые лучше жить в mini app.

## Цели

ContenCore V2 должен обеспечивать:

- одну общую платформу для множества клиентов;
- одну общую PostgreSQL БД на всех клиентов;
- tenant-aware архитектуру на всех уровнях;
- отдельный внутренний админский контур;
- отдельный клиентский контур;
- централизованное управление функциями по клиентам;
- централизованные обновления и багфиксы;
- безопасное подключение клиентских Telegram-ботов;
- постепенный перенос клиентского UX в mini app;
- сохранение Notion как удобного операционного интерфейса, но не как primary
  system database.

## Не-цели

В рамках этой архитектуры не допускаются следующие направления:

- отдельный форк системы под каждого клиента;
- отдельная база данных на каждого клиента по умолчанию;
- сохранение Notion как primary source of truth;
- передача bot token через web-форму mini app;
- хранение основной бизнес-модели в SQLite и JSON;
- повторное превращение новой версии в один большой неструктурированный
  runtime-монолит.

## Ключевые архитектурные решения

В рамках этого RFC принимаются следующие решения:

1. ContenCore V2 строится как multi-tenant платформа.
2. Все клиенты хранятся в одной общей PostgreSQL БД.
3. Все tenant-aware таблицы обязаны содержать `tenant_id`.
4. Внутренний и клиентский контуры разделяются по ролям, интерфейсам и правам.
5. У клиента должен быть собственный Telegram-бот.
6. Для внутренних администраторов создаётся отдельный admin bot.
7. Для внутренних администраторов создаётся отдельный admin mini app.
8. Для клиентов создаётся отдельный client mini app.
9. Управление функциями клиента выполняется через данные в БД и admin UI.
10. Notion остаётся внешним operational workspace, но не primary database.
11. Секреты и токены не вводятся через mini app формы.
12. Bot token клиента принимается через message-based onboarding flow.
13. Сервисная архитектура строится как набор микросервисов с чёткими
    зонами ответственности.

## Почему одна БД на всех клиентов

Для этой платформы одна общая PostgreSQL БД является правильной базовой
стратегией.

Это решение даёт:

- единый источник правды;
- централизованные миграции;
- простую аналитику по системе;
- удобное управление tenant-ами;
- единое место для feature management;
- единый админский интерфейс;
- более предсказуемую эксплуатацию;
- меньшую стоимость сопровождения.

Правильная модель здесь:

- одна БД;
- много tenant-ов внутри неё;
- логическая изоляция по `tenant_id`;
- строгая авторизация и фильтрация доступа по tenant-контексту.

Неправильная модель для этого этапа:

- отдельная БД на каждого клиента;
- отдельный код на клиента;
- отдельные таблицы под каждого клиента;
- смешивание tenant-данных без явной идентификации.

## Роль Notion

Notion нужно сохранить, но его роль должна быть пересмотрена.

В новой архитектуре Notion:

- не является primary system database;
- не хранит основную системную модель платформы;
- не определяет, какие функции включены у клиента;
- не является местом хранения ролей, токенов, feature flags или billing state.

В новой архитектуре Notion:

- используется как удобный внешний интерфейс для контентной команды;
- выступает как operational workspace;
- может показывать карточки, сценарии, контент-план, статусы и вспомогательные
  представления;
- синхронизируется с системной БД через отдельный sync-сервис.

Primary source of truth должен быть в PostgreSQL.

## Целевой стек технологий

### Backend

- `Python 3.13+`
- `FastAPI`
- `Pydantic`
- `SQLAlchemy 2.x` или `SQLModel`
- `Alembic`
- `Redis`
- `Celery` или `RQ`

### Database

- `PostgreSQL` как единая primary system database

### Telegram layer

- `python-telegram-bot` либо отдельный Telegram gateway service
- `Telegram Bot API`
- `Telegram Mini Apps`

### Frontend

- `TypeScript`
- `Next.js` или `React + Vite`
- `Telegram WebApp SDK`

### Infrastructure

- `Docker`
- `Docker Compose` для dev и stage
- `Nginx` или ingress / API gateway
- centralized logging
- metrics
- alerting

### AI и внешние интеграции

- `Anthropic`
- `Groq`
- `ElevenLabs`
- `HeyGen`
- `Meta APIs`
- `YouTube APIs`
- `VK APIs`
- `Notion API`
- `ffmpeg` и `ffprobe`

## Общая целевая архитектура

ContenCore V2 должен состоять из набора микросервисов, объединённых общей
tenant-моделью и общей PostgreSQL БД.

Базовые сервисы:

1. `api-gateway`
2. `auth-service`
3. `tenant-service`
4. `bot-service`
5. `admin-ui-service`
6. `client-ui-service`
7. `content-service`
8. `media-service`
9. `publish-service`
10. `billing-service`
11. `integration-service`
12. `notion-sync-service`
13. `worker-service`
14. `audit-service`

## Архитектурные слои

### 1. Presentation Layer

Сюда входят:

- admin bot;
- client bots;
- admin mini app;
- client mini app;
- внешние webhook endpoints.

Этот слой не должен содержать тяжёлую бизнес-логику. Его задача:

- принять запрос;
- определить tenant и роль;
- провалидировать доступ;
- передать запрос в нужный backend use case.

### 2. Application Layer

Сюда входят use cases и orchestration flow.

Этот слой отвечает за:

- запуск сценариев;
- вызов domain-сервисов;
- выбор policy;
- запуск интеграций;
- запуск jobs и publish flow.

### 3. Domain Layer

Сюда входят:

- tenant model;
- content model;
- billing model;
- publish model;
- feature model;
- access model;
- policy model;
- bot onboarding model.

Этот слой должен описывать правила платформы, а не детали транспорта.

### 4. Infrastructure Layer

Сюда входят:

- PostgreSQL;
- Redis;
- Notion API;
- Telegram API;
- AI providers;
- media binaries;
- очереди;
- файловые и object storage integrations.

## Описание сервисов

### API Gateway

Единая входная точка для HTTP-трафика.

Ответственность:

- маршрутизация;
- auth context;
- rate limiting;
- tracing;
- routing для mini app;
- routing для внешних webhooks.

### Auth Service

Отвечает за аутентификацию и авторизацию.

Ответственность:

- internal admin auth;
- client auth;
- Telegram WebApp validation;
- RBAC;
- permission matrix;
- service-to-service auth.

### Tenant Service

Главный сервис tenant-модели.

Ответственность:

- создание tenant-а;
- хранение tenant metadata;
- хранение tenant settings;
- хранение feature flags;
- хранение capabilities;
- хранение policy bindings;
- tenant lifecycle;
- tenant preflight;
- tenant activation/deactivation.

### Bot Service

Главный сервис Telegram-ботов.

Ответственность:

- привязка Telegram-бота к tenant-у;
- валидация bot token;
- обновление bot metadata;
- управление bot profile;
- обработка update;
- tenant-aware routing update в use cases;
- запуск onboarding flow для нового client bot.

### Admin UI Service

Mini app для внутренних администраторов платформы.

Ответственность:

- список клиентов;
- карточка клиента;
- переключение модулей;
- переключение capabilities;
- настройка policy;
- настройка branding;
- настройка интеграций;
- запуск preflight;
- просмотр health, jobs, audit log;
- просмотр статуса токенов и bot profile.

### Client UI Service

Mini app для клиента.

Ответственность:

- работа с контентом;
- карточки и сценарии;
- запуск генерации;
- публикация;
- approval;
- media actions;
- безопасные self-service действия.

### Content Service

Отвечает за контентную модель.

Ответственность:

- идеи;
- карточки;
- сценарии;
- посты;
- статусы;
- рубрики;
- prompts;
- workflow content lifecycle.

### Media Service

Отвечает за media pipeline.

Ответственность:

- voiceover;
- avatar generation;
- B-roll;
- image generation;
- subtitles;
- final assembly;
- media metadata;
- media job state.

### Publish Service

Отвечает за публикацию на площадки.

Ответственность:

- Telegram publishing;
- YouTube publishing;
- Instagram publishing;
- VK publishing;
- TikTok publishing;
- retry logic;
- state machine публикации;
- audit публикаций.

### Billing Service

Отвечает за биллинг.

Ответственность:

- тарифы;
- балансы;
- лимиты;
- ledger;
- списания;
- billing events;
- feature monetization rules.

### Integration Service

Отвечает за внешние API и секреты.

Ответственность:

- OAuth flows;
- token metadata;
- secret refs;
- token rotation state;
- health checks integrations;
- unified external wrappers.

### Notion Sync Service

Отвечает за синхронизацию с Notion.

Ответственность:

- export в Notion;
- import из Notion;
- mapping tenant databases;
- schema validation;
- sync карточек и представлений.

### Worker Service

Отвечает за фоновые задачи.

Ответственность:

- AI jobs;
- media jobs;
- publish jobs;
- sync jobs;
- retries;
- cron tasks;
- webhook processing.

### Audit Service

Отвечает за журналирование значимых действий.

Ответственность:

- кто включил модуль;
- кто выключил capability;
- кто сменил policy;
- кто привязал token;
- кто изменил billing;
- кто выполнил risky action.

## Модель межсервисного взаимодействия

В системе должны быть два основных способа взаимодействия:

### Синхронное взаимодействие

Используется для:

- admin UI запросов;
- client mini app запросов;
- auth checks;
- tenant reads;
- lightweight orchestration.

Транспорт:

- HTTP API между сервисами;
- внутренние typed contracts;
- correlation id для трассировки.

### Асинхронное взаимодействие

Используется для:

- media pipeline;
- publish pipeline;
- sync jobs;
- retries;
- long-running operations;
- webhook event processing.

Транспорт:

- очередь задач через Redis + worker system;
- event-driven паттерны для domain events, если это оправдано.

## Модель данных

База данных одна. Клиентов много. Все системные данные должны храниться в
PostgreSQL.

Базовые сущности:

- `tenants`
- `tenant_modules`
- `tenant_capabilities`
- `tenant_policies`
- `tenant_integrations`
- `tenant_bots`
- `tenant_users`
- `users`
- `roles`
- `permissions`
- `content_items`
- `content_statuses`
- `content_assets`
- `media_jobs`
- `publish_jobs`
- `publish_targets`
- `billing_accounts`
- `billing_ledger`
- `external_tokens`
- `secret_refs`
- `notion_bindings`
- `feature_flags`
- `audit_logs`
- `webhook_events`

## Принципы проектирования БД

1. PostgreSQL является primary source of truth.
2. Tenant isolation реализуется через `tenant_id`.
3. Все tenant-aware запросы обязаны быть tenant-scoped.
4. Миграции схемы централизованы.
5. Feature toggles живут в БД.
6. Billing state живёт в БД.
7. Bot bindings живут в БД.
8. Secrets не хранятся в plain text в бизнес-таблицах.
9. Все административные изменения должны быть аудируемыми.

## Tenant-модель

Tenant должен быть полноценной системной сущностью.

Tenant включает:

- id;
- slug;
- display name;
- status;
- owner;
- assigned admins;
- bot bindings;
- notion bindings;
- enabled modules;
- enabled capabilities;
- feature flags;
- billing plan;
- policy set;
- branding;
- publishing settings;
- media settings;
- prompt pack;
- integration readiness.

## Feature Management

Feature management должен быть встроен в платформу как обязательный слой.

Он должен включать:

- modules;
- capabilities;
- feature flags;
- policies;
- rollout groups;
- readiness state.

Через admin mini app внутренний админ должен иметь возможность:

- включить модуль;
- выключить модуль;
- включить capability;
- выключить capability;
- выбрать policy set;
- активировать интеграцию;
- отключить интеграцию;
- перевести tenant в другой режим готовности;
- запустить preflight.

## Модель Telegram-ботов

### Client Bot

У каждого клиента должен быть свой Telegram-бот.

Это нужно для:

- tenant-specific branding;
- независимого клиентского UX;
- отдельной настройки команд, описания и профиля;
- tenant-specific поведения;
- дальнейшего масштабирования.

### Admin Bot

У платформы должен быть отдельный внутренний admin bot.

Он нужен для:

- admin entrypoint;
- доступа к админским действиям;
- системных уведомлений;
- безопасного запуска onboarding и admin flows.

## Почему bot token должен приходить сообщением

Bot token является чувствительным секретом.

Поэтому:

- token нельзя вводить через mini app форму;
- token нельзя отдавать браузерному клиенту;
- token нельзя логировать в frontend;
- token должен идти через message-based secure flow;
- после получения token должен сразу валидироваться;
- после валидации token должен сохраняться через secret storage.

Рекомендуемый onboarding flow:

1. Админ создаёт tenant в admin mini app.
2. Система создаёт onboarding session.
3. Админ или клиент отправляет bot token в admin bot.
4. Bot service валидирует token через Telegram API.
5. Token сохраняется как secret reference.
6. Система подтягивает `bot_id`, `username`, display metadata и текущий статус.
7. Админ завершает настройку клиента в admin mini app.

## Mini App модель

### Admin Mini App

Это главный интерфейс внутренних администраторов.

Основные экраны:

- список клиентов;
- карточка клиента;
- модули;
- capabilities;
- policy;
- branding;
- integrations;
- billing;
- bot profile;
- readiness;
- preflight;
- jobs;
- audit log.

### Client Mini App

Это главный интерфейс клиента.

Основные сценарии:

- создание и просмотр контента;
- работа с карточками;
- запуск генерации;
- публикация;
- approval;
- работа с медиа;
- история операций;
- ограниченные self-service настройки.

## Роли и права

Нужна строгая role model.

Базовые роли:

- `platform_super_admin`
- `platform_admin`
- `tenant_admin`
- `tenant_operator`
- `tenant_editor`
- `tenant_viewer`
- `service_account`

Правила:

- клиент видит только свой tenant;
- внутренний админ может управлять многими tenant-ами;
- внутренние админы и клиентские пользователи разделены;
- все чувствительные действия журналируются;
- все действия в admin UI проходят permission checks.

## Security-модель

### Обязательные правила

1. Secrets живут отдельно от бизнес-таблиц.
2. Bot tokens, OAuth tokens и API keys не вводятся через mini app.
3. Все чувствительные действия пишутся в audit log.
4. Все public webhook endpoints валидируются и защищаются от replay.
5. Все service-to-service вызовы аутентифицируются.
6. Все tenant-scoped endpoints обязаны проходить tenant authorization.
7. Внутренние админские действия должны быть отделены от клиентских действий.

### Secret Storage

В БД должны храниться:

- secret reference;
- тип секрета;
- владелец;
- статус валидности;
- дата последней проверки;
- дата ротации;
- audit metadata.

Сами секреты должны храниться:

- в secret manager;
- либо в отдельном защищённом storage;
- либо в шифрованном backend для секретов.

## Контракт модульности

Каждый модуль должен иметь:

- `module_id`;
- версию;
- required services;
- required config;
- required capabilities;
- required integrations;
- required secrets;
- required UI toggles;
- required preflight checks.

Если модуль выключен для tenant-а:

- UI не показывает его;
- backend не запускает его use cases;
- jobs по нему не создаются;
- callbacks и команды по нему не должны выполняться;
- integration calls по нему не должны стартовать.

## Preflight и readiness

Перед переводом tenant-а в production-ready состояние должны проверяться:

- валидность tenant record;
- валидность bot binding;
- наличие обязательных capabilities;
- наличие обязательных modules;
- readiness publish targets;
- readiness Notion binding;
- readiness billing setup;
- readiness media pipeline;
- readiness external integrations;
- readiness security configuration.

Без успешного preflight tenant не должен активироваться.

## Observability

Новая архитектура обязана иметь нормальную наблюдаемость.

Минимально обязательны:

- структурированные логи;
- correlation id;
- tenant-aware tracing;
- job status tracking;
- health endpoints;
- integration health monitoring;
- error rate monitoring;
- audit log.

## Контур обновлений

У платформы должен быть один общий release path.

Это означает:

- код обновляется централизованно;
- миграции применяются централизованно;
- feature toggles контролируют включение функций;
- rollout может идти по tenant-группам;
- bugfix делается один раз и раскатывается всем нужным tenant-ам.

## Миграционная стратегия

Переход к ContenCore V2 должен быть поэтапным.

### Этап 1. Ввести PostgreSQL как primary DB

Задачи:

- спроектировать tenant-aware schema;
- вынести системные сущности из SQLite и JSON в PostgreSQL;
- завести `tenant_id` в целевой модели;
- перенести модули, capabilities, policies и bot bindings в БД.

### Этап 2. Ввести Tenant Service и admin-контур

Задачи:

- создать tenant-service;
- создать role/permission model;
- создать admin bot;
- создать admin mini app;
- реализовать CRUD tenant-ов;
- реализовать переключение modules/capabilities.

### Этап 3. Ввести Bot Service

Задачи:

- отвязать систему от предположения "один бот = одна система";
- внедрить multiple client bots;
- реализовать secure bot token onboarding;
- реализовать bot metadata sync;
- реализовать tenant-aware update routing.

### Этап 4. Ввести Client Mini App

Задачи:

- перенести клиентский UX из Telegram-only модели;
- разделить admin UI и client UI;
- дать клиенту удобный self-service интерфейс.

### Этап 5. Разнести домены по микросервисам

Первыми выносить:

- tenant-service;
- bot-service;
- billing-service;
- integration-service;
- notion-sync-service.

Следом:

- content-service;
- media-service;
- publish-service;
- worker-service.

### Этап 6. Стабилизировать release model

Задачи:

- ввести migration discipline;
- зафиксировать service contracts;
- зафиксировать rollout и rollback process;
- зафиксировать preflight как обязательный production gate.

## Открытые вопросы

Эти вопросы ещё требуют отдельного уточнения:

1. Используем ли один общий API gateway или BFF-слой отдельно для admin и client
   mini app.
2. Нужен ли отдельный object storage слой с S3-совместимым API для медиа.
3. Оставляем ли `python-telegram-bot` как основной transport layer или выносим
   Telegram routing в отдельный gateway service.
4. Делаем ли event bus как отдельный слой позже или ограничиваемся очередями на
   первом этапе.
5. Где именно хранится secret storage: Vault, cloud secrets manager или
   self-hosted encrypted backend.

## Альтернативы

### 1. Улучшить текущий монолит без смены data model

Отклонено, потому что это не решает проблему общей БД, tenant management,
feature control и admin mini app.

### 2. Делать отдельную БД на каждого клиента

Отклонено, потому что это сильно повышает стоимость эксплуатации и ломает
централизованную платформенную модель.

### 3. Оставить Notion основной базой

Отклонено, потому что Notion удобен как интерфейс, но не как primary system
database.

### 4. Строить новый большой монолит

Отклонено, потому что это снова приведёт к перегрузке одного runtime и росту
хаоса.

## Риски

У этой архитектуры есть цена:

- микросервисы дороже в эксплуатации;
- миграция займёт заметное время;
- потребуется более жёсткая дисциплина по контрактам;
- потребуется зрелая observability-модель;
- Notion sync потребует аккуратной границы ответственности.

Но для модели "много клиентов в одной платформе" этот путь оправдан.

## Критерии успеха

RFC считается реализованным, когда выполняются условия:

1. Новый клиент подключается без изменения backend-кода.
2. Все tenant-ы живут в одной PostgreSQL БД.
3. Внутренний админ может через admin mini app включить или выключить функцию
   клиенту.
4. У клиента есть свой Telegram-бот, привязанный к tenant-у.
5. Bot token клиента передаётся через защищённый message flow.
6. Notion остаётся внешним интерфейсом, но не primary source of truth.
7. Клиентская часть доступна через client mini app.
8. Все чувствительные действия аудируются.
9. Tenant-aware access control исключает пересечение клиентских данных.
10. Feature management полностью управляется через данные и admin UI.

## Итоговая рекомендация

ContenCore V2 следует строить как multi-tenant платформу с:

- одной общей PostgreSQL БД;
- tenant-aware data model;
- микросервисной архитектурой;
- отдельным admin bot;
- отдельным admin mini app;
- отдельными client bots;
- отдельным client mini app;
- централизованным feature management;
- Notion как внешним operational workspace;
- строгим onboarding flow для bot token;
- обязательным preflight перед активацией tenant-а.

Главное следствие этого RFC:

**новый клиент должен подключаться через данные, роли, настройки, токены,
capabilities, policies, toggles и preflight, а не через изменение кода.**
