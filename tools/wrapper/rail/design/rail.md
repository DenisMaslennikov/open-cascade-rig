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
| `SessionRow` | In `SessionList` | Unread dot slot, title, bound card (`card #172`) or `no card bound`, `running` or `Needs input`, `?` marker, relative time |
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
| `colorTextSecondary` | `#93a1b5` | Row metadata (board id, bound card, relative time), inactive tab, footer words |
| `colorTextMuted` | `#6b7686` | Captions and the workspace placeholder only; nothing the owner must act on |
| `colorBorder` | `#273140` | Outlines, language switch, notice |
| `colorSplit` | `rgba(230, 237, 247, 0.08)` | Row and section separators |

Tokens to add: none. Label chips take their hex from Vikunja at runtime (the sketch uses `#5b8ff9`, `#3fbf6f`, `#e8a33d` only as samples). The chip text always stays `colorText`; the label colour is only the border and the dot, so any hex stays legible.

Non-colour values reused: `radiusSm` 4, `radiusMd` 8, `spaceXs` 8, `spaceSm` 12, `fontSizeSm` 12, `fontSizeMd` 14, `motionFast` 120 ms. Type scale: 12 px is the floor (row metadata, chips, blocked word, footer, counters), 13 px for row titles and tabs, 14 px for the active tab and brand. Nothing is below 12 px. Relative times are 12 px, `colorTextSecondary`, tabular figures, `white-space: nowrap`.

## States (each is a `data-state` block in the page)

- `cards`: five cards. Two with chips, one blocked (`data-blocked="true"`, warning icon plus the word `Blocked`), one with a very long title cut after two lines.
- `sessions`: five sessions, newest message first (Sessions counter 5, Cards counter 0). Two unread, one waiting, four bound to a card (`card #172 · …` style sub-lines). The fourth row carries `data-state="focus"`, which draws the 2 px `#5b8ff9` ring as keyboard focus does.
- `unread`: the header pill shows the total with a green dot, and a 3 px green strip on the top edge of the rail is the browser-tab style attention marker. The footer shows `dashboard 401` as a degraded upstream.
- `waiting`: `data-waiting="true"` row with a `?` marker in a ring, the words `Needs input`, an elevated background and a 3 px left bar. Four cues, so colour is not the only one.
- `empty`: no cards in Plan; icon, title and one sentence on what to do. Counters read 0.
- `loading`: seven skeleton rows in the real row geometry, counters show a dash, footer says `connecting`. The pulse stops under `prefers-reduced-motion`.
- `error`: `role="alert"` notice naming the failed upstream, saying that sessions keep running, with a `Retry` button. Both tabs stay clickable; the footer dot turns red with the word `unreachable`.
- Row interaction states: default, hover (tint wash plus title to `colorPrimaryHover`), focus (ring), unread (title weight 600 plus dot plus brighter time), waiting, blocked. Active tab: `data-active="true"`, weight 600, 14 px, 3 px `#5b8ff9` underline, `colorPrimaryTint` wash, counter in `colorText` on a `colorPrimary` pill; the inactive tab is 13 px weight 400 `colorTextSecondary`.
- Counter contract: every tab button carries `data-count="N"`, N being the number of rows of that type rendered in the same state block (0 for empty, loading and error, and for the tab of the other type). The visible counter shows the same N. The keyboard-focus sample row uses `data-focus="true"`, so `data-state` stays reserved for the seven state blocks.

## Accessibility

- All controls are real `button` elements: rows, tabs, language segments, project picker, retry. Tabs use `role="tablist"` / `role="tab"` with `aria-selected`.
- Focus ring: 2 px `#5b8ff9`, drawn inside the element (`outline-offset: -2px`) so the rail's `overflow: hidden` never clips it.
- Unread has a text alternative (`aria-label` on the dot) and a bolder title, not only a green dot. Waiting has the `?` marker, the words and a left bar. Blocked has an icon and a word.
- The error notice is `role="alert"`; the attention strip is `role="status"`; the loading list is `aria-busy` and the skeleton rows are `aria-hidden`.
- Muted text (`#6b7686`) is used only for captions with no actionable information, never below 12 px. Anything the owner must act on uses secondary or primary text.
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
| `rail.session.card` | card #{id} | карточка #{id} |
| `rail.session.noCard` | no card bound | без карточки |
| `rail.session.idle` | idle | простаивает |
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

---

# Desktop screen (pass 3)

Artefact: `desktop.html` (1440×900 frames, inline CSS and SVG, no JavaScript, dark). `rail.html` stays as the mobile sketch. The desktop rail reuses every mobile component at desktop density (52 px rows, 14 px titles, 40 px tabs). Same tokens; no new colours. Only new non-colour values: `radiusLg` 12 (dialogs), monospace stack for code, tool lines and the prompt, scrim `rgba(15, 20, 26, 0.72)` (`colorBgLayout` at 72 %).

## Layout

Window = rail 340 px + workspace (rest). Workspace column: `ChatHeader` 56 px, `Transcript` (flex, scrolls; text column capped at 800 px and centred, 16/26 body), `Composer` (textarea 2 rows growing to 6 = 156 px). Works down to 1280 px: only the transcript column shrinks. Dialogs 540 px (`ConfirmStart`) and 560 px (`PromptEditor`), centred over the scrim.

## Ready marker (three places, never colour alone)

| Place | Markup | Word / icon |
|---|---|---|
| Header | `ReadyBadge` `data-ready="true"`, amber dot with halo, `colorBgElevated`, amber border | `Ready — waiting for you` |
| Rail row | `SessionRow` `data-ready="true"`: elevated background, 4 px amber left bar, bold title, `.rmark` pill | pill with check icon and `Ready` |
| Rail top | `AttentionStrip` `data-kind="ready"`: 30 px strip, 3 px amber top edge | bell icon, `Ready`, count sentence |

Unread is the quiet variant: green dot and bold title on the row, a 3 px green strip with no text (`sr`-only label), green dot in the header pill. Ready outranks unread: if both apply, the strip is amber. `ReadyBadge` has three values: ready (amber), working (spinner, `Working…`), idle (grey dot, `Idle`).

## Component inventory (new)

| `data-component` | Placement | Notes |
|---|---|---|
| `AttentionStrip` | Top edge of `Rail` | `data-kind="ready"` (loud, words) or `"unread"` (3 px, quiet); `role="status"` |
| `Workspace` | Right of the rail | Column: header, transcript, composer; or `EmptyWorkspace` |
| `ChatHeader` | Top of workspace, 56 px | Title, bound card button `#184 · title`, model chip (mono), `ReadyBadge`, quiet `Stop` and `Open card` |
| `ReadyBadge` | In `ChatHeader` | See above |
| `Transcript` | Middle, `role="log"` | Messages, `divider` per day, `ToolCall`, `ReasoningBlock`, `JumpChip` |
| `ToolCall` | In transcript | `details`: tool name (mono), one-line input summary (ellipsis), result line with check or cross; body is the output |
| `ReasoningBlock` | In transcript | Collapsed `details`, italic, `colorTextSecondary` |
| `JumpChip` | Floating, bottom-centre of transcript | Shown only when scrolled up; auto-scroll resumes on click |
| `Composer` | Bottom of workspace | Textarea, `Send`, hint `Enter to send · Shift+Enter newline`, char counter, slash hint line, disabled reason above the box |
| `EmptyWorkspace` | `no-session` state | One sentence on what to do |
| `ConfirmStart` | `data-modal="confirm-start"` | Card `#184 · title`, warning that a new session starts, system prompt (5 lines visible, fade, `Show all`), `Cancel` / `Start session` (primary, initial focus), Escape closes |
| `PromptEditor` | `data-modal="prompt-editor"` | Mono textarea, placeholders `{{card_id}}` `{{card_title}}` `{{project}}` drawn as chips (overlay behind a transparent textarea), help line, `Reset to default` left, `Cancel` / `Save` right, `n / 2000` counter; Save returns to `ConfirmStart` |

`SessionRow` additions: when the bound card was started from the rail it shows `card #N`; ready rows add the `Ready` pill before the time. `CardRow` is unchanged; the card being confirmed is marked `data-pressed="true"`.

## States

`default` (session #184 selected and scrolled up so the jump chip shows; typed `/he` shows the slash hint; #181 ready in the rail), `ready` (selected session waiting: three markers, composer disabled with a lock and the reason), `confirm-start`, `prompt-editor`, `no-session`, `loading` (skeletons in rail and workspace, counters `–`, composer disabled: `Connecting to the dashboard terminal…`), `error` (board down, rendered in Russian as the longest-string check: rail notice with `Retry`, sessions and chat fully usable). Counters follow the mobile contract: `data-count` equals the rows rendered in the block.

Disabled composer reasons: ready (`Locked: … waiting for your answer above`), working (`… press Stop`), loading (`Connecting …`). The reason sits above the box, is linked with `aria-describedby`, and `Send` is disabled too.

## Accessibility

Real `button`, `details/summary`, `textarea` controls. Focus ring 2 px `colorPrimary`; inside dialogs a 2 px gap ring (`box-shadow`) so it survives `overflow: hidden`. Dialogs: `role="dialog"`, `aria-modal`, labelled by the title, focus starts on the primary action (editor: the textarea), Escape closes, focus returns to the clicked card. Timestamps are `opacity: 0` until hover or focus-within (still in the DOM). Body text 16 px, nothing below 12 px.

## Catalogue keys (new, `en` / `ru`)

| Key | en | ru |
|---|---|---|
| `chat.ready.badge` | Ready — waiting for you | Готово — ждёт вас |
| `chat.ready.short` | Ready | Готова |
| `chat.ready.strip` | {n} session is waiting for you | Сессия ждёт вашего ответа: {n} |
| `chat.status.working` / `idle` | Working… / Idle | Работает… / Простаивает |
| `chat.stop` / `chat.openCard` | Stop / Open card | Остановить / Открыть карточку |
| `chat.jump` | Jump to latest | К последнему сообщению |
| `chat.reasoning` | Reasoning · {s} s | Рассуждение · {s} с |
| `chat.day.today` / `yesterday` | Today / Yesterday | Сегодня / Вчера |
| `composer.placeholder` | Message Hermes | Сообщение для Hermes |
| `composer.send` | Send | Отправить |
| `composer.hint` | Enter to send · Shift+Enter newline | Enter — отправить · Shift+Enter — новая строка |
| `composer.slash` | Slash command — type a name, e.g. | Команда — введите имя, например |
| `composer.off.ready` | Locked: Hermes finished its turn and is waiting for your answer above. | Заблокировано: Hermes закончил ход и ждёт вашего ответа выше. |
| `composer.off.working` | Hermes is working on this turn. You can write again when it is done, or press Stop. | Hermes работает над этим ходом. Писать можно после завершения или нажмите «Остановить». |
| `composer.off.loading` | Connecting to the dashboard terminal… | Подключение к терминалу дашборда… |
| `workspace.empty.title` | No session selected | Сессия не выбрана |
| `workspace.empty.body` | Pick a card on the left to start a Hermes session, or open a session from the Sessions tab. | Выберите карточку слева, чтобы начать сессию Hermes, или откройте сессию на вкладке «Сессии». |
| `confirm.title` | Start a Hermes session? | Начать сессию Hermes? |
| `confirm.warn` | A new Hermes session will be started for this card and will run in this project. It begins with the prompt below. | Для этой карточки будет запущена новая сессия Hermes в этом проекте. Она начнётся с промпта ниже. |
| `confirm.prompt` / `showAll` / `editPrompt` | System prompt / Show all ({n} lines) / Edit prompt for this card | Системный промпт / Показать всё ({n} строк) / Изменить промпт для этой карточки |
| `confirm.cancel` / `confirm.start` | Cancel / Start session | Отмена / Начать сессию |
| `prompt.title` | Prompt for card #{id} | Промпт для карточки #{id} |
| `prompt.help` | this text becomes the first message of the session | этот текст станет первым сообщением сессии |
| `prompt.placeholders` | Placeholders | Подстановки |
| `prompt.reset` / `cancel` / `save` | Reset to default / Cancel / Save | Сбросить к умолчанию / Отмена / Сохранить |

The sketch shows the rest of the `rail.*` keys unchanged; Russian strings were checked in the `error` state.

## Transport note

The chat pane is the dashboard's own terminal: WebSocket `GET /api/pty?resume=<session_id>` of `hermes-dashboard` (port 9119, basic auth) drives a real `hermes --tui` through xterm.js; `GET /api/pty?fresh=1&cwd=<path>` starts a new one. The ready state is not guessed from terminal bytes: it comes from the gateway's session status events. **The sketch assumes** the transcript, tool blocks, composer and badge are native components fed by structured session events, and that a status event exists for "turn finished, waiting for the owner"; **the implementation must confirm** that the gateway emits that event (and its inverse) per session, and decide whether the xterm pane or the native transcript is the visible surface, since an xterm pane cannot render the collapsible tool blocks, hover timestamps or the disabled composer drawn here.
