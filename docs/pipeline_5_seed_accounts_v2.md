---
name: pipeline-5-seed-accounts-v2
description: Seed-список IG-аккаунтов для виральный трендспоттер Pipeline 5, v2. Синтез из deep-research workflow (Claude+Gemini+GPT-5, 30 субагентов с adversarial-verification) + ручной верификации Артёма (Кучмент, Соколовский, Хартманн).
metadata:
  type: research
  date: 2026-06-08
  branch: maksim-bot
  supersedes: pipeline_5_seed_accounts.md
---

# Pipeline 5 — Seed-список v2 (deep-research синтез)

> Версия 2 после deep-research workflow + ручной верификации Артёмом. Содержит **adversarial-verified handles** (через 30 субагентов с Claude+Gemini+GPT-5), стоп-лист по уголовным делам 2024-2025, и **2 критичных сюрприза** про устаревание изначальной стратегии.

## ⚠️ Главные сюрпризы исследования (поменять архитектуру P5)

### 🔴 Сюрприз 1. Глэмпинги массово ушли из Instagram в VK/TG/Dzen/Rutube
Проверены 3 топ-объекта из списка (A-Ferma, Forest Lake, Эндемик Роза Хутор) — **ни у одного нет IG handle на официальном сайте.** Только VK + Telegram + Dzen + Rutube + MAX.

| Объект | На сайте указаны | Instagram |
|---|---|---|
| A-Ferma | VK (`vk.com/a_ferma`) + TG (`t.me/aferma`) + Dzen + Rutube | **нет** |
| Forest Lake | VK (`forestlake_glam`) + TG + MAX | handle `@forestlake_glamping` существует (подтверждён прямым визитом), но не на сайте |
| Эндемик (Роза Хутор) | TG (`@endemicglamping`) + VK + MAX | **нет** |

**Вывод**: для мониторинга глэмпинг-ниши **только IG недостаточно**. Минимум добавлять VK Видео + Telegram-каналы. Это переделывает архитектуру источников P5 (нужны Apify VK Scraper + TG-collector через MTProto / Telethon).

### 🔴 Сюрприз 2. У владельцев картинг-центров РФ — нет личных IG-аккаунтов
Проверены Primo Karting, E-GO Karting, Forza/MIKS Karting — **только корпоративные аккаунты, ни одного личного блога владельца.** Единственный публично известный «владелец+картинг+личный IG» = **Тимати (Timati Karting)** — но он рэпер-предприниматель, не «реальный бизнес».

Имя владельца E-GO Karting нашли — **Алексей Сергеевич Журавский** (ИП). Можно искать его IG вручную по имени.

**Вывод**: нишу «владельцы картинг-центров с личным блогом про бизнес» — практически нет на российском IG. Либо ставим корпоративные аккаунты (менее виральные), либо отказываемся от этой ниши вовсе и фокусируемся на «личный бренд предпринимателя» (где материала много).

---

## ✅ Подтверждённое ядро (личный бренд предпринимателей)

Все handles подтверждены через как минимум один из источников: Forbes, AdInBlog, TrendHERO, прямая проверка субагентом, или независимая проверка Артёмом.

| # | IG handle | Имя | Тематика | Followers | Подтверждение |
|---|---|---|---|---|---|
| 1 | [@rybakov_igor](https://instagram.com/rybakov_igor) | Игорь Рыбаков | Технониколь ($2.4B), бизнес-коучинг + философия | ~1M | Forbes билл-блогеры 2025 |
| 2 | [@olegtorbosov](https://instagram.com/olegtorbosov) | Олег Торбосов | Whitewill, элитная недвижимость | не уверен | подтверждение Артёма |
| 3 | [@grebenuk.m](https://instagram.com/grebenuk.m) | Михаил Гребенюк | ES-clinic, Аномалия, подкаст | ~681k | AdInBlog |
| 4 | [@michael_kuchment](https://instagram.com/michael_kuchment) | **Михаил Кучмент** | **Сооснователь Hoff, подкаст «Бизнес на салфетке»** | **~166k** | Подтверждение Артёма + WebSearch |
| 5 | [@sokolovskiy](https://instagram.com/sokolovskiy) | **Александр Соколовский** | **Tooligram/Scout/Honey Teddy Hair, Forbes 30-30 (2024), подкаст @podcast_sokol** | **~544k** | Подтверждение Артёма + WebSearch |
| 6 | [@forbes.russia](https://instagram.com/forbes.russia) | Forbes Russia | Бизнес-Reels | не уверен | подтверждение Артёма |
| 7 | [@forbes.club.russia](https://instagram.com/forbes.club.russia) | Forbes Club Russia | Бизнес-сообщество | не уверен | подтверждение Артёма |
| 8 | [@forbes_education](https://instagram.com/forbes_education) | Forbes Russia Education | Бизнес-обучение | не уверен | подтверждение Артёма |
| 9 | [@forbes.woman.russia](https://instagram.com/forbes.woman.russia) | Forbes Woman Russia | Бизнес-женщины | не уверен | подтверждение Артёма |
| 10 | [@olegtinkov](https://instagram.com/olegtinkov) ⚠️ | Олег Тиньков | Tinkoff founder | ~1.35M | AdInBlog. ⚠️ Антивоенная позиция с 2022, отказ от РФ-гражданства — для тюменского клиента может быть репутационно сложно |
| 11 | [@oskar_hartmann](https://instagram.com/oskar_hartmann) | **Оскар Хартманн** | KupiVIP founder, инвестор, спикер, благотворитель | **~720k** | WebSearch + подтверждение Артёма |
| 12 | [@linguamarina](https://instagram.com/linguamarina) | **Марина Могилко** | LinguaTrip / Silicon Valley Girl, эмиграция и предпринимательство в США | ~44k (доп.: @siliconvalleygirl, @linguatriprussian) | WebSearch + подтверждение Артёма |

**Финальное ядро: 12 handles** (включая 4 Forbes-аккаунта).

**Кандидаты под верификацию** (Артём пока не подтвердил, требуют проверки на тон):
- @katya_golden (Екатерина Касатова, ~947k) — LUVU beauty 103M ₽/год, Forbes 30-30 (2026)
- @margo.savchuk (Margo Savchuk, ~770k) — general business training, **требует проверки на инфоцыганские маркеры**
- @telyakovtv (Антон Теляков, ~451k), @ana.mavricheva (Ана Мавричева, ~303k), @anna_finance (Анна Громова, ~175k), @aleksandrsusedko (Александр Суседко, ~94k) — из AdInBlog топа, Артём не знаком, не включаем без верификации
- Максим Спиридонов (Netology), Аркадий Морейнис («Тёмная сторона») — handle не подтверждён, в основном TG

**Из Forbes billionaire-bloggers 2025** (но в основном Telegram, не IG):
- Sergey Kolesnikov (Технониколь партнёр) — TG ~1.5k followers, малая аудитория
- Dmitry Alekseev (DNS) — TG `@AlekseevDNS`, контент «marketplace counterfeit + Vladivostok + running» — ровно «реальный бизнес из практики», но IG не подтверждён

---

## 🔴 Стоп-лист (исключить — токсичные для бренда «реальный бизнес»)

Все имена ниже подтверждены через 2+ независимых источника (РБК, Forbes, msk1.ru, MSK Inc.Russia) как фигуранты уголовных дел 2024-2025.

| # | Handle | Имя | Проблема | Статус 2025-2026 |
|---|---|---|---|---|
| 1 | @ayazshabutdinov | Аяз Шабутдинов (Like Centre) | **Приговор 7 лет колонии + 5 млн ₽ штрафа** (31 окт 2025) | Осуждён, 113 эпизодов мошенничества, ущерб >57 млн ₽ |
| 2 | @portnuagin | Дмитрий Портнягин (Трансформатор, Club 500) | Уголовное дело: 124 млн ₽ налогов + отмывание | Домашний арест с апреля 2024, в окт.2024 смягчено на ограничение действий |
| 3 | @elenablinovskaya_official | Елена Блиновская (Марафон желаний) | Отмывание + уклонение, долг 1.4 млрд ₽ | СИЗО, банкрот с ноября 2024 |
| 4 | @lerchek | Чекалины (Лерчек) | Незаконный перевод 250+ млн ₽ за рубеж, фитнес-марафоны | Домашний арест, новое дело с окт.2024 |
| 5 | (БМ закрыта) | Михаил Дашкиев (Бизнес Молодость, новый проект «Юниты») | БМ закрыта 2020, репутационный шлейф «самый скандальный инфобизнес РФ» (Forbes) | Сам признаёт «дистанцируется от шлейфа» |
| 6 | @petr.osipov | Пётр Осипов (БМ) | Сместился в «философию и саморазвитие», ~1.5M followers | Не подходит под «бизнес из практики» |
| 7 | @tatyanabakalchuk | Татьяна Бакальчук (Wildberries) | Handle есть, но **аккаунт мёртвый** (0 постов, 257 followers) | Технически handle, фактически мониторить нечего |

**Регуляторный сигнал**: Госдума готовит закон о реестре онлайн-коучей с лицензированием + криминальной ответственностью. Системный риск всей «марафон-бизнес» категории — позиционирование «реальный бизнес без курсов» Максима выигрывает.

---

## 🏔 Глэмпинг — ревизия стратегии

Учитывая Сюрприз 1 (массовый исход в VK/TG), приоритеты:

### Подтверждённые IG handles (из v1)
| # | Handle | Объект | Followers | Активность |
|---|---|---|---|---|
| 1 | [@les_glamping](https://instagram.com/les_glamping) | ЛЕС Глэмпинг и СПА Сочи (топ-5 РФ) | ~120k | Reels |
| 2 | [@vdohaltay](https://instagram.com/vdohaltay) | Эко-отель ВДОХ Горный Алтай | ~69k | Reels |
| 3 | [@lesimorecamp_altay](https://instagram.com/lesimorecamp_altay) | Лес и Море Алтай | ~21k | Reels |
| 4 | [@a_ureki](https://instagram.com/a_ureki) | А У РЕКИ Подмосковье | ~25k | не уверен |
| 5 | [@pod_kronami](https://instagram.com/pod_kronami) | Под кронами A-frame Подмосковье | ~24k | не уверен |
| 6 | **[@forestlake_glamping](https://instagram.com/forestlake_glamping)** | **Forest Lake СПА (Ленобласть)** — найден субагентом | не уверен | не уверен |

### VK/TG аккаунты глэмпингов (если расширяем источники)
| Объект | VK | Telegram |
|---|---|---|
| A-Ferma | vk.com/a_ferma | t.me/aferma |
| Forest Lake | vk.com/forestlake_glam | (есть) |
| Эндемик Роза Хутор | vk.com/endemicglamping | @endemicglamping |

### Из топ-20 vc.ru (Москва + область) — handles НЕ подтверждены
Ферма «A-Ferma», Эко-отель «Под кронами», «А у реки», «Forest», Nordic A-frame, «MyShelters», А-фреймы «Casa Ruza», Woody Village Riverside, «Доминго Дача», «Pavlove Village», «Берёзовая Роща», «Лесополье»

Из других регионов (по списку Артёма): Forest Life, Хюгге Кэмп (Карелия), Вилла Ягель (Ладога), BOHO CAMP, The Lagom (Вуокса), Harland Village (Карелия), Echo Altai, Lucky Glamping, Bageo Glamping.

**Рекомендация**: либо потратить 1-2 часа на ручной обход их сайтов чтобы вытащить handles из футеров, **либо** расширить источники P5 до VK+Telegram и не зависеть от IG.

---

## 🏎 Картинг — 5 корпоративных как low-priority

**Решение Артёма**: личных блогов владельцев нет, поэтому фокус — не картинг как ниша вдохновения, а наблюдение **сигнальное** (вдруг кто-то из конкурентов запустит виральную кампанию). 5 корпоративных аккаунтов как low-priority источник (виральность маловероятна, но мониторим на всякий случай).

| # | Handle | Объект | Регион |
|---|---|---|---|
| 1 | [@primokarting](https://instagram.com/primokarting) | Primo Karting | СПб |
| 2 | [@lemans_karting](https://instagram.com/lemans_karting) | Le Mans Karting Club | Москва ЦАО |
| 3 | [@egokarting.ru](https://instagram.com/egokarting.ru) | E-GO Karting | Москва |
| 4 | [@kartodrom_lider](https://instagram.com/kartodrom_lider) | Гоночная Трасса Лидер | — |
| 5 | [@electro_karting](https://instagram.com/electro_karting) | Электро Картинг | Казань |

В скоринге P5 — **отдельный лейбл `priority=low`**, виральность считается по их собственному baseline (низкий, поэтому даже малое отклонение может быть сигналом). Если за 2-3 месяца ни одного кандидата из этой ниши не уйдёт в дайджест — удаляем.

---

## 📊 Финальная численная сводка

| Категория | Приоритет | Подтверждено | Под верификацию | Исключено |
|---|---|---|---|---|
| Личный бренд предпринимателей | **high** | **12** | 5-7 | 7 (по уголовкам / неактивные) |
| Глэмпинги (IG) | medium | **6** | — | — (VK/TG не расширяем) |
| Картинг (корпоративные) | **low** | 5 | — | личные блоги отсутствуют |

**Итого для MVP**: **23 аккаунта** (12 личный бренд + 6 глэмпинг + 5 картинг). Достаточно для старта Pipeline 5 MVP.

---

## 🎯 Финальные решения для MVP Pipeline 5

✅ **Подтверждено Артёмом 8 июня 2026:**
1. Запускаем с **12 якорными личных-бренд аккаунтов**: Рыбаков, Торбосов, Гребенюк, **Кучмент, Соколовский, Хартманн, Могилко**, Тиньков ⚠️, + 4 Forbes-аккаунта.
2. **Глэмпинг — 6 IG-якорей, не расширяем** на VK/TG. «Не паримся».
3. **Картинг — 5 корпоративных как low-priority**. Виральности от них не ждём, мониторим сигнально.
4. **Стоп-лист обязательный** — встроить в pre-filter перед показом Максиму. Даже виральный Reel от Шабутдинова/Портнягина/Лерчек/Блиновской не показывать.
5. Расширение списка — после первых 2-3 недель работы по реальным данным (что виральное, что не зашло).

---

## 📚 Источники (через workflow adversarial-verified)

### Forbes
- [Forbes: Самые активные российские миллиардеры-блогеры 2025](https://www.forbes.ru/milliardery/552970-mysli-vsluh-samye-aktivnye-rossijskie-milliardery-blogery-2025-goda)
- [Forbes: 10 перспективных российских блогеров моложе 30 — 2026](https://www.forbes.ru/svoi-biznes/559075-10-perspektivnyh-rossijskih-blogerov-i-komikov-moloze-30-let-2026)
- [Forbes: 10 перспективных российских блогеров моложе 30 — 2025](https://www.forbes.ru/svoi-biznes/537220-10-perspektivnyh-rossijskih-blogerov-i-komikov-moloze-30-let-2025)
- [Forbes: реакция предпринимателей на приговор Шабутдинову](https://www.forbes.ru/svoi-biznes/549109-pravovoj-perebor-kak-predprinimateli-otreagirovali-na-prigovor-aazu-sabutdinovu)
- [Forbes: Смерть «Бизнес Молодости»](https://www.forbes.ru/karera-i-svoy-biznes/407759-smert-biznes-molodosti)
- [Forbes: Александр Соколовский — Forbes 30 до 30 (2024)](https://30-under-30.forbes.ru/2024/510184-aleksandr-sokolovskij)

### Уголовные дела / стоп-лист
- [msk1.ru: Дела на блогеров в 2024 (свод: Лерчек, Блиновская, Шабутдинов, Портнягин)](https://msk1.ru/text/criminal/2025/01/09/74923145/)
- [Inc. Russia: Крах инфопредпринимателей. Изнанка империй Аяза и Лерчек](https://incrussia.ru/understand/infobusiness_again/)
- [РБК Life: Дмитрий Портнягин (Трансформатор) — обыски, дело, биография](https://www.rbc.ru/life/news/6617af719a794713e2108f3f)

### IG-каталоги и метрики
- [AdInBlog: ТОП Предпринимателей в Instagram](https://adinblog.ru/топ/категории/предприниматели/)
- [AdInBlog: ТОП Бизнес-тренеров в Instagram](https://adinblog.ru/топ/категории/бизнес-тренеры/) (для negative filtering)
- [trendHERO: бизнес-блогеры Instagram](https://trendhero.io/ru/blog/business-bloggers/)
- [HypeAuditor: Top-1000 IG influencers Russia](https://hypeauditor.com/top-instagram-all-russia/)

### Кучмент / Соколовский (ручная верификация)
- [Michael Kuchment (@michael_kuchment) Instagram](https://instagram.com/michael_kuchment/)
- [«Бизнес на салфетке» — Apple Podcasts](https://podcasts.apple.com/us/podcast/бизнес-на-салфетке/id1734414152)
- [Sokolovskiy Alexander (@sokolovskiy) Instagram](https://instagram.com/sokolovskiy/)

### Глэмпинги (для добивки)
- [vc.ru: Топ-20 A-frame глэмпингов Подмосковья 2024](https://vc.ru/travel/1616629-glempingi-v-podmoskove-top-20-luchshih-treugolnyh-a-frame-domikov-dlya-otdyha-v-mo-reiting-2024-goda)
- [aferma.info — A-Ferma](https://aferma.info/glamping-v-podmoskovie)
- [forestlake.ru — Forest Lake](https://forestlake.ru/)
- [endemic-glamping.ru — Эндемик](https://endemic-glamping.ru/)

### Картинг
- [E-GO Karting (egokarting.ru)](https://egokarting.ru/)
- [Cartings.ru — рейтинг картинг-клубов РФ](https://cartings.ru/)
