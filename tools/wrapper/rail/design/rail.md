# Hermes wrapper rail: design handoff

Card 184, subtask 2. Artefact: `rail.html` (inline CSS and SVG, no JavaScript, dark theme). This is a sketch for the owner to iterate on; only the left rail is designed. The workspace to its right is a placeholder.

## Layout

- Rail: 340 px wide (allowed 320-360), full height of the workspace, background `colorBgNav`, 1 px `colorBorder` outline. Header and footer are fixed; the list scrolls inside the rail.
- Vertical order: attention strip (only when unread) > `RailHeader` > `ProjectPicker` > `TabBar` > list or state body > `RailFooter`.
- Row height: sessions are exactly 44 px (8 px vertical padding). Card rows are `min-height: 44px` and grow with content: a two-line title adds 16 px, the chip line adds 20 px. This is deliberate: the brief asks for two-line titles and chips, which cannot fit in 44 px. Measured heights in the sketch: 44-70 px.
- Separators: 1 px `colorSplit`, no zebra, no shadows.

## Component inventory

| `data-component` | Placement | Contents |
|---|---|---|
| `Rail` | Left edge of the workspace | Container for everything below |
| `RailHeader` | Top of the rail, 48 px | Brand mark and name, total-unread pill, `LanguageSwitch` |
| `LanguageSwitch` | Right end of the header | Two segment buttons `EN` / `RU`, `aria-pressed` marks the active one; it is a control, not a translation |
| `ProjectPicker` | Under the header, one 32 px row | Label, slug (`cascade-rig`), chevron; opens a listbox |
| `TabBar` | Under the picker | Two buttons `data-tab="cards"` / `data-tab="sessions"` with counters |
| `CardList` | Body of the Cards tab | `ul` of `CardRow` |
| `CardRow` | In `CardList` | Board id `#184`, title (2 lines max, ellipsis), label chips, blocked marker, relative time. Click starts a Hermes session bound to the card |
| `SessionList` | Body of the Sessions tab | `ul` of `SessionRow`, sorted by last message, newest first |
| `SessionRow` | In `SessionList` | Unread dot slot, title, bound card id or `no card`, `running` or `Needs input`, `?` marker, relative time |
| `RailFooter` | Bottom of the rail | Two upstream states, `board` and `dashboard`: dot, name, word |

## Tokens used (OCCC dark map, no new colours)

| Token | Value | Use |
|---|---|---|
| `colorBgLayout` | `#0f141a` | Page canvas |
| `colorBgNav` | `#131a22` | Rail background, footer |
| `colorBgContainer` | `#161c24` | Project picker, workspace placeholder |
| `colorBgCard` | `#1b222c` | Error notice |
| `colorBgElevated` | `#202836` | Counters, skeleton bars, waiting row |
| `colorPrimary` | `#5b8ff9` | Active tab underline, focus ring, retry button, brand mark |
| `colorPrimaryHover` | `#7aa6ff` | Hover text of rows, tabs and buttons |
| `colorPrimaryTint` | `rgba(91, 143, 249, 0.12)` | Active language segment, active counter |
| `colorPrimaryTintHover` | `rgba(91, 143, 249, 0.08)` | Row hover and focus wash |
| `colorSuccess` | `#3fbf6f` | Unread dot, attention strip, healthy upstream dot |
| `colorWarning` | `#e8a33d` | Waiting marker and bar, blocked word, degraded upstream dot |
| `colorDanger` | `#e5534b` | Unreachable upstream dot, error notice bar and icon |
| `colorText` | `#e6edf7` | Titles, active labels |
| `colorTextSecondary` | `#93a1b5` | Ids, inactive tabs, footer words |
| `colorTextMuted` | `#6b7686` | Relative times, sub-lines, captions |
| `colorBorder` | `#273140` | Outlines, language switch, notice |
| `colorSplit` | `rgba(230, 237, 247, 0.08)` | Row and section separators |

Tokens to add: none. Label chips take their hex from Vikunja at runtime (the sketch uses `#5b8ff9`, `#3fbf6f`, `#e8a33d` only as samples). The chip text always stays `colorText`; the label colour is only the border and the dot, so any hex stays legible.

Non-colour values reused: `radiusSm` 4, `radiusMd` 8, `spaceXs` 8, `spaceSm` 12, `fontSizeSm` 12, `fontSizeMd` 14, `motionFast` 120 ms. Two sizes outside the scale are used on purpose: 13 px for row titles and 11 px for chips and sub-lines. Add `fontSizeXs` (11) only if the owner keeps them.

## States (each is a `data-state` block in the page)

- `cards`: five cards. Two with chips, one blocked (`data-blocked="true"`, warning icon plus the word `Blocked`), one with a very long title cut after two lines.
- `sessions`: five sessions, newest message first. Two unread, one waiting, three bound to a card. The fourth row carries `data-state="focus"`, which draws the 2 px `#5b8ff9` ring as keyboard focus does.
- `unread`: the header pill shows the total with a green dot, and a 3 px green strip on the top edge of the rail is the browser-tab style attention marker. The footer shows `dashboard 401` as a degraded upstream.
- `waiting`: `data-waiting="true"` row with a `?` marker in a ring, the words `Needs input`, an elevated background and a 3 px left bar. Four cues, so colour is not the only one.
- `empty`: no cards in Plan; icon, title and one sentence on what to do. Counters read 0.
- `loading`: seven skeleton rows in the real row geometry, counters show a dash, footer says `connecting`. The pulse stops under `prefers-reduced-motion`.
- `error`: `role="alert"` notice naming the failed upstream, saying that sessions keep running, with a `Retry` button. Both tabs stay clickable; the footer dot turns red with the word `unreachable`.
- Row interaction states: default, hover (tint wash plus title to `colorPrimaryHover`), focus (ring), unread (title weight 600 plus dot plus brighter time), waiting, blocked. Active tab: weight 600, 2 px underline, brighter counter.

## Accessibility

- All controls are real `button` elements: rows, tabs, language segments, project picker, retry. Tabs use `role="tablist"` / `role="tab"` with `aria-selected`.
- Focus ring: 2 px `#5b8ff9`, drawn inside the element (`outline-offset: -2px`) so the rail's `overflow: hidden` never clips it.
- Unread has a text alternative (`aria-label` on the dot) and a bolder title, not only a green dot. Waiting has the `?` marker, the words and a left bar. Blocked has an icon and a word.
- The error notice is `role="alert"`; the attention strip is `role="status"`; the loading list is `aria-busy` and the skeleton rows are `aria-hidden`.
- Muted text (`#6b7686`) is used only for non-essential metadata: times and captions. Anything the owner must act on uses secondary or primary text.
- Times never wrap (`white-space: nowrap`) and use tabular figures; counters do the same.
- Russian text is longer: titles clamp, the project slug ellipsises, and the footer wraps onto a second line instead of overflowing.

## Catalogue keys (`src/i18n/en.json` and `ru.json`, key for key)

| Key | en | ru |
|---|---|---|
| `rail.aria` | Hermes rail | Панель Hermes |
| `rail.language` | Language | Язык |
| `rail.project.label` | Project | Проект |
| `rail.tab.cards` | Cards | Карточки |
| `rail.tab.sessions` | Sessions | Сессии |
| `rail.counter.cards` | Cards in Plan | Карточек в Plan |
| `rail.counter.running` | Running sessions | Запущенных сессий |
| `rail.unread` | {n} unread | {n} непрочитанных (plural forms one/few/many) |
| `rail.card.blocked` | Blocked | Заблокирована |
| `rail.session.noCard` | no card | без карточки |
| `rail.session.running` | running | работает |
| `rail.session.needsInput` | Needs input | Нужен ответ |
| `rail.session.unreadLabel` | Unread messages | Есть непрочитанные сообщения |
| `rail.session.waitingLabel` | Waiting for your input | Ждёт вашего ответа |
| `rail.time.minutes` | {n} min | {n} мин |
| `rail.time.hours` | {n} h | {n} ч |
| `rail.time.days` | {n} d | {n} д |
| `rail.footer.board` | board | доска |
| `rail.footer.dashboard` | dashboard | дашборд |
| `rail.footer.synced` | synced {time} ago | синхронизировано {time} назад |
| `rail.footer.connected` | connected | подключён |
| `rail.footer.connecting` | connecting | подключение |
| `rail.footer.unreachable` | unreachable | недоступна |
| `rail.footer.unauthorized` | 401 | 401 |
| `rail.empty.title` | No cards in Plan | Нет карточек в Plan |
| `rail.empty.body` | Move a card of {project} to the Plan column on the board and it will show up here. | Перенесите карточку проекта {project} в колонку Plan на доске, и она появится здесь. |
| `rail.loading` | Loading | Загрузка |
| `rail.error.title` | Board unreachable | Доска недоступна |
| `rail.error.body` | Cards could not be loaded from the board. Sessions keep running; the last sync was {time} ago. | Не удалось загрузить карточки с доски. Сессии продолжают работать; последняя синхронизация была {time} назад. |
| `rail.error.retry` | Retry | Повторить |

Relative times and counters go through `src/lib/format.ts`; the markup in `rail.html` only shows the result.
