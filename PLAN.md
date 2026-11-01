# План реализации

Обновлено: 2026-10-31. Детали решений — `DECISIONS.md`.

## P0. Инфраструктура (полдня) — СДЕЛАНО
- [x] Vikunja в docker: `192.168.31.5:3456`, проект `cascade`, 8 колонок в нужном порядке (`~/soft/vikunja/docker-compose.yaml`).
- [x] Пользователь `oswe`, креды+JWT: `~/.config/vikunja/creds.json` (0600).
- [x] Адаптер `board.py` (`~/soft/vikunja/board.py`): columns / create / move / comment / update / get / delete; `python3 board.py smoke` → OK.
- [x] Перенос карточки через bucket-эндпоинт проверен на живом инстансе.
- Замечания: API-токены Vikunja 2.7 отвергают старую схему permissions → адаптер логинится по паролю и обновляет JWT на 401. `bucket_id` в `GET /tasks/{id}` не заполняется — колонку знает оркестратор, доска это проекция.

## P1. Скил-оркестратор (1–2 дня)
- [x] Схема плана `schema/plan.schema.json` + `tools/validate_plan.py` (проверен на валидном и плохом плане).
- [x] Гейты `tools/gate.py`: `check-diff`, `frozen-tests`, `expect-fail` (с отсечкой «инфраструктурного» падения), `expect-pass`.
- [x] Реестр ролей `roles.yaml` (модель + `key_ref` на каждую роль, бюджеты).
- [x] `plan_task.py` — роль архитектора скриптом: запрос заказчика + карта репо + правила/ADR → план (`ready`) или вопросы (`needs_clarification`), валидация через `validate_plan.py`, запись в ledger. Проверено на OCCC тремя прогонами (2× вопросы, 1× план из 12 подзадач).
- [ ] `cascade-orchestrator`: фазы, вызов LiteLLM, запуск исполнителя, стоимость, синк с доской.
- [ ] `cascade-arbiter`: промпт + схема вердикта (`winner/rationale/action/confidence`), двухступенчатый.
- [ ] `cascade-reviewer`: обвязка pr-agent (URL PR → отчёт → карточка).

## P1.5. Скил создания проекта — СДЕЛАНО
- [x] Скил `cascade-new-project`: SKILL.md, `references/github-setup.md`, `references/openhands-rules.md`, `templates/{AGENTS.md,README.md,rules.md,ci.yml}`, `scripts/bootstrap.sh`.
- [x] Бутстрап проверен трижды: обычный путь, через прокси 192.168.31.6:10809, фолбэк без сети; `adr lint` и `pytest` зелёные во всех трёх.
- [x] ADR через adrkit (`npx @adrkit/cli`, схема 0.2.0, заголовки только ASCII, `affects` связывает решения с путями).
- [x] Правила агентов: `.agents/rules/<группа>.md` (workflow, code_style, design, testing), а `AGENTS.md` — индекс «файл | о чём | когда читать». OpenHands SDK сам читает `AGENTS.md`/`CLAUDE.md`/`.cursorrules`/`GEMINI.md` из корня workspace, группы подключаются ссылками из таблицы.
- [x] Прогон на реальном проекте: `open-capsuleer-command-center` — репо, 8 ADR, правила, compose-контур, CI зелёный; `plan_task.py` выдаёт планы и вопросы.

## P2. Полигон EVE — первый срез (офлайн, без ESI)
- [x] Роль `chief_architect` (D18): модель opus, вызывается человеком, контекст собирает оркестратор (`--context repo,schema,roadmap,file:PATH`). Скрипт `chief_architect.py`, обязательная модель в проекте, ledger `-r chief_architect -f _advice`. Первый вопрос отработан (формат контракта + состав гейта).
- [x] Контракт схемы БД перенесён в репозиторий и открыт PR: https://github.com/DenisMaslennikov/open-capsuleer-command-center/pull/2 (`docs/schema/`, `.agents/rules/database.md`, строка в `AGENTS.md`); CI зелёный, карточка #129 закрыта.
- [x] Контракт схемы БД спроектирован архитектором (Opus, 14 вызовов): 13 агрегатов, 67 таблиц, 600 колонок, 80 FK, 14 enum. Артефакты — `~/.local/state/cascade/occc/schema/{schema.json,schema.dbml,schema.sql,schema.md,contract/}`. Скрипт — `schema_design.py` (`--reemit` пересобирает без модели). Проверено: DBML парсится `@dbml/core`, DDL применяется к пустой `postgres:16` без ошибок. Карточка `#129` на доске в Review, реализация разбита на DB1–DB7.
- [x] Реализация схемы разбита на карточки DB1–DB7 и **связана с фичами**: 76 связей на доске (`board_links.py`, карта — `schema/links.json`), каждая DB-карточка блокирует те фичи, которым нужны её таблицы; в фичах это видно как «blocked by».
- [x] Роадмап продукта архитектором: `roadmap.py` (11 этапов, 77 общих задач + бриф дизайнера), валидация пройдена, стоимость в ledger (`-f _roadmap`). Источник правды — `~/.local/state/cascade/occc/roadmap/roadmap.json`.
- [x] Роадмап заведён на доске: проект **OCCC — OpenCapsuleerCommandCenter** (`/projects/6/26`), 89 карточек (11 этапов + 77 задач + карточка дизайнера), метка на каждый этап, повторный сид идемпотентен. Скрипт — `board_seed.py`.
- [ ] `t1` парсер SDE-подмножества (blueprints/materials) → типизированные модели + фикстуры.
- [ ] `t2` `material_requirements(bp, me, runs)` — чистая функция; краевые: ME 0/10, дробные, рекурсия компонентов.
- [ ] `t3` `production_time(bp, te, runs, facility_bonus)`.
- [ ] `t4` `profit(plan, prices)` — ISK, налоги, комиссии.
- [ ] `t5` HTTP-слой + страница «план производства».

## P3. Прогон каскада и метрики
- [x] Первый полный цикл на s0-t1 (формулы SP): архитектор → спецификатор (тесты+заглушки) → исполнитель (OpenHands, glm-flash) → гейты → pr-agent → мерж в `feat/skills-sp-for-level` → PR #3 в main. Три подзадачи, 42 теста, CI зелёный. Стоимость цикла ≈ $7 вместе с дизайном.
- [x] Дизайнер как роль: `design_task.py`, standalone HTML + handoff (PR #4). opus на этой задаче дважды не отдал ответ (обрезка и пустой content) — артефакт сделан на `qwen3-coder-flash`; решение по модели дизайнера за владельцем.
- [ ] Прогнать 3 задачи по полному циклу: план → тесты → код → добивка → аудит → приёмка (закрыта 1 из 3).
- [ ] Сравнить исполнителей на одинаковых задачах и тестах: OpenHands vs aider (токены/время/качество).
- [ ] Метрики: стоимость задачи, число попыток, время до зелёного, время ревью человеком, доля задач с реально пойманным дефектом, выход за `files_allowed`.
  - Факты первого цикла: исполнитель 62–92 с и ~$0.26–0.43 за подзадачу, правок после ревью — одна (докстринг модуля), выходов за `files_allowed` — ноль, попыток больше одной не потребовалось ни разу (кроме перегенерации спеки с неверной арифметикой).

## P4. Решение по проекту №2 (фреймворк)
Только после P3. Кандидатная база: `deepagents` (оркестрация) + `openhands-agent-server` (изолированные воркспейсы, REST/WS) + k8s + RabbitMQ как транспорт оркестратора (агенты напрямую не общаются).

## Открытые вопросы
- ~~Репозиторий для EVE-полигона: локальный bare-repo или GitHub?~~ Решено: GitHub, приватный; эфемерный контейнерный раннер на песочнице готов к привязке (`~/ci-runner/attach-ci-runner.sh <owner/repo>`, см. DECISIONS D16).
- Нужен ли ESI на второй итерации (сеть, рейт-лимиты) или хватит фикстур.
- Формат Таблицы Истинности: markdown в карточке или отдельный JSON + рендер.
