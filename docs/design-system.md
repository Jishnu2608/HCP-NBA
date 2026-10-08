# Design system

The web application uses one visual language for all six roles. Everything below lives in two files:

- `frontend/src/index.css`: design tokens (colours, shadows, layering), light and dark themes, base styles, focus ring, motion.
- `frontend/src/ui.tsx`: the components every page is built from.

Pages do not use raw colours or hand-made controls. If a page needs something new, add it to `ui.tsx`.

## Direction

The interface is aimed at enterprise healthcare and pharma users in the United States. It should feel calm and trustworthy, readable at a glance, and dense only where the data needs it. Avoid decoration that carries no information: no gradients on controls, no glass effects, no animation for its own sake.

## Colour

A refined clinical blue on cool slate, in both themes. Colours are semantic tokens defined as CSS variables: light values on `:root`, dark values on `:root[data-theme="dark"]`. They are mapped into Tailwind as `bg-surface`, `text-ink-muted`, `bg-primary` and so on. The dark set is designed separately (deep blue-slate surfaces, restrained blue), not an inversion or a filter. A brand or theme change is an edit to these tokens only.

| Token | Light | Dark | Use |
|---|---|---|---|
| `canvas` | #F6F8FB | #0D1520 | Page background |
| `subtle` | #EEF2F7 | #1B2A3B | Secondary background, insets, fact tiles |
| `surface` | #FFFFFF | #162231 | Cards, panels, menus, drawers |
| `ink` / `ink-muted` / `ink-subtle` / `ink-disabled` | #172033 / #526174 / #5D6B80 / #A8B1BD | #E8EEF5 / #B5C1CF / #8795A6 / #5F6C7B | Text |
| `line` / `line-strong` | #D8E0E8 / #C3CED9 | #29394A / #3A4B5D | Borders |
| `primary` (hover, active) | #2563A6 (#1F5792, #194A7D) | #4C8CCB (#62A0DA, #3D78B0) | Primary actions, active navigation, links, selected states, the key action of a recommendation |
| `on-primary` | #FFFFFF | #08121D | Text on primary |
| `primary-soft` | #E8F1FA | #1A334C | Selected rows, informational highlights |
| `sage` / `sage-ink` | #EEF2F7 / #456A8E | #1B2A3B / #789AB8 | Secondary blue on a neutral: secondary chips, non-primary states |
| `nav` (hover, active) | #132238 (#1B3049, #214B73) | #0A121C (#142333, #193A58) | Sidebar and dark bands |
| `ok` / `warn` / `bad` / `info` | text #2B7553 / #8F5E15 / #A8403E / #2F6FA3 | #68B58D / #D5A34D / #E27673 / #68A4D4 | Status text and icons |
| `*-fill` | #2F7D5A / #B7791F / #B94A48 / #2F6FA3 | same as text | Status bars, dots, segments |
| `*-soft` | #E8F4EE / #FFF5DE / #FBEAEA / #E8F2FA | #153328 / #352B18 / #3A2021 / #152D43 | Status backgrounds |

`accent` exists for compatibility and equals `primary`: there is one call-to-action colour.

Rules:

- Blue is reserved for primary actions, active navigation, important links, selected states and the core action of a recommendation. Cards, tables, forms and analytics containers stay neutral. A recommendation card is neutral with a blue key action and a thin blue top rule; its "Why this action?" tiles are neutral with a soft-blue icon tile.
- Status colours carry meaning only: green success / low risk / approved; amber warning / medium risk / pending; red error / blocked / high risk / rejected; blue information.
- Contrast: every text token passes WCAG AA (4.5:1) on the surfaces and tints it is used on, in both themes (checked for 24 pairs; lowest 4.72 light, 4.77 dark). In light mode the reference values for warning (#B7791F), error (#B94A48) and muted text (#7A8798) fall below 4.5:1 as small text on their own tinted backgrounds, so the text tokens use darker shades of the same hues and the reference values are used for fills.
- Charts: `--series-1` clinical blue (also the engine), `--series-2` amber, `--series-3` slate teal, neutral `--series-baseline`.

### Status semantics

Status is never shown by colour alone: every badge has an icon and words.

| Meaning | Tone | Icon and label |
|---|---|---|
| High / medium / low adherence risk | bad / warn / ok | triangle / circle / check, "High risk" etc. |
| Recommendation pending review | info | clock, "Pending review" |
| Approved | brand | check, "Approved" |
| Sent / responded | sage / ok | send / double check |
| Blocked by a safeguard | bad | shield, "Blocked"; blocked rows are tinted and the action is struck through |
| Rejected / superseded | neutral | x / history |
| MLR approved / pending / rejected / expired | ok / warn / bad / bad | shield / clock / x / calendar |
| Consent granted / not granted | ok / bad | check / x |

Charts use `--series-1..3` (eucalyptus, ochre, slate blue) and a neutral `--series-baseline` for comparisons.

## Theme switching

- One application-wide state (`frontend/src/theme.tsx`): `data-theme="light" | "dark"` on `<html>` selects the token set.
- Precedence: the user's explicit choice, saved in `localStorage` (`nba.theme`, read and written through `session.ts`), then the operating system's `prefers-color-scheme`. While no choice is saved, the app follows OS changes live.
- No flash: an inline script in `index.html` applies the same rule before the first paint. During a switch, transitions are suppressed for two frames so nothing animates its colours.
- One toggle component, `ThemeToggle` (sun in dark mode, moon in light mode, labelled "Switch to light/dark theme"), shown in the app top bar, the landing header, the sign-in / sign-up / verification pages and the standalone error pages. Every instance drives the same state.

## Errors and fallbacks

One layout for every failure: `ErrorPanel` in `ui.tsx` (icon, error code, title, plain explanation, recovery actions). `ErrorState` picks the kind from a failed request; `pages/ErrorPage.tsx` holds the standalone screen and the crash boundary.

| Kind | When | Title | Actions |
|---|---|---|---|
| 404 | Unknown address; record not found or outside the account's scope | Page not found / Not found | Dashboard (signed in) or home, Go back |
| 400 | Malformed link or request (API 400 / 422) | This request isn't valid | Dashboard, Go back |
| 401 | Signed out on an app page, or session ended | Please sign in | Sign in (returns to the page if the role allows it) |
| 403 | Page or resource outside the role | Access restricted | Dashboard, Go back |
| 500 | Server error or a crash while rendering | Something went wrong | Try again, Dashboard, Go back |
| Data | Network failure or a panel that could not load | Unable to load this information (or "… could not be loaded") | Try again, Dashboard |

- Signed-out visitors: an address that is a real app page goes to sign-in with a "Please sign in to continue" notice; any other address gets the standalone 404 screen (brand, theme switch, footer).
- Signed in: errors render inside the shell, so navigation, the account menu and the theme switch keep working. The crash boundary resets on the next navigation.
- "Go to my dashboard" uses the account's home route from the server.
- Nothing internal is shown: no stack traces, exception names, SQL or raw server text. The API's own 4xx messages (written for people) are shown for failed actions; 5xx, network failures and validation dumps get plain wording. Summary figures show "—" until their data has loaded, never a misleading 0.

## Typography

- Family: Public Sans (variable), self-hosted through `@fontsource-variable/public-sans`. No external font requests.
- Page title 28px semibold, tight tracking. Card title 15px semibold. Body 14–15px. Captions 13px. Section labels use sentence case, not uppercase.
- Numbers use tabular figures (`.tabular`).
- Headings use `text-wrap: balance`, paragraphs `text-wrap: pretty`.

## Layout system

### Spacing scale

One scale for the whole product (Tailwind units of 4px):

| Gap | Where |
|---|---|
| 16px (`gap-4`) | Between cards inside a grid (KPI rows, bento grids, rationale tiles) |
| 24px (`gap-6`) | Between panels and columns on detail pages |
| 32px (`mb-8`) | After a KPI row |
| 40px (`mt-10`) | Between dashboard sections (`SectionHeader`) |
| 20 / 24px | Card padding on phones / from 640px |

### KPI cards (`KpiCard`, `KpiGrid` in `ui.tsx`)

Every key figure uses the same structure, so cards in a row line up whatever their text:

1. Title region: always two lines tall (13px, line-height 20px). Longer titles are clamped; the full text is in the tooltip.
2. Icon: a fixed 32px tile at the top right, tinted with the card's tone.
3. Value: one line, 28px tabular figures, counting up when it first appears.
4. Hint region: always one line tall (12px). Hints are kept short enough to fit.

`KpiGrid` chooses the columns from the number of cards, so every count has an intentional layout with no empty cells: 3 -> 1/3 columns; 4 -> 2/4; 5 -> 2 / (3 + 2 on tablets) / 5; 6 -> 2/3/6. An odd last card on two-column phones spans the row. With `to`, a KPI card becomes a link: it lifts on hover or focus and an arrow appears.

### Bento grid (`layout.tsx`)

Dashboards and other dense screens use one grid: 1 column on phones, 2 from 768px, 12 from 1280px. Cells come in four sizes only:

| Size | Columns at 1280px+ | Use |
|---|---|---|
| `full` | 12 | Activity feeds, a summary that needs the width |
| `wide` | 8 | The primary item of a row (main chart) |
| `half` | 6 | Paired items of equal weight |
| `narrow` | 4 | Supporting items beside a `wide` cell |

Rows always add up to 12 (8 + 4, 6 + 6, 12). Below 1280px the cells go full width, because charts and lists need the width; `pairOnTablet` lets small cells sit two-up from 768px. Cards stretch to the tallest cell in the row, and a footer `note` is pinned to the bottom, so neighbouring cards end level. When a card would be mostly empty (for example a trend with one data point), the layout changes instead: the related card takes the row.

Components: `BentoGrid`, `BentoCard` (icon tile, title, description, optional "View all" link, footer note), `BentoCell` (a cell for content that brings its own card, stretched to the row), `SectionHeader`, `InsightRow` (finding plus figure), `BarRow` (label, figure, proportion bar). From 1280px a cell can be two rows tall (`rows={2}`) so a long list sits beside two stacked supporting cards; their bottoms line up with it.

### 360 pages

| Row | Patient 360 | HCP 360 |
|---|---|---|
| 1 | Next best action (wide) + consent on record (narrow) | Next best action (wide) + profile (narrow) |
| 2 | One full-width card per therapy (PDC, MPR, gap, refill, risk, 12-month supply timeline) | What the HCP engages with (wide) + response by channel (narrow) |
| 3 | Outreach timeline (wide, two rows) + response by channel and care team (narrow, stacked; side by side on tablets) | Engagement timeline (full) |

On phones the cells stack in that order, so the recommendation always comes first.

### Dashboard reading order

Overview KPIs -> Insights (engine versus earlier outreach, what stands out) -> Recommendations (status by audience, what the engine proposes) -> Adherence and engagement -> Compliance and safeguards -> Recent activity (audit, for roles that may read it).

### Recommendation hierarchy

Everywhere a recommendation appears it reads in the same order: "Next best action" label -> action -> "Why this action?" -> channel -> timing -> compliance status -> expected outcome -> primary action. On the recommendation page the reasons follow as a bento of evidence tiles (profile signals, action, channel, timing, compliance; a held-back option spans the row in amber).

## Motion

Built on GSAP (`frontend/src/motion.tsx`, approved motion plan of 2026-10-08). Motion explains hierarchy, a change of state or where something went; it never decorates. Performance comes first: transform and opacity only (SVG stroke for drawing lines), no animation sets React state, every tween lives in a `useGSAP` scope and is reverted on unmount.

Tokens: micro 0.18 s (press, small state), standard 0.32 s (cards, panels, drawers), expressive 0.6 s (landing page only). Easing `power2.out` in, `power2.in` out.

| Building block | Where | Meaning |
|---|---|---|
| `useEntrance` | `KpiGrid` (after the heading), `BentoGrid` (after the figures), the sign-in panel | The page arrived, in reading order. At most 8 items move (4 on touch); runs once, never on refetch |
| `Morph` | Status badges, request status chips, MLR badges, content version badges | The state changed (never on first render) |
| `useChangeHighlight` | Data tables with `motionSig`, request lists, HCP consultations, HCP work | A new row, or a row whose state changed, lifts in once; other rows never move |
| `playExit` | Drawers, the phone navigation drawer, the account menu, toasts | Where the surface went; then it unmounts. Backdrops only fade (no blur) |
| `AnimatedNumber` | KPI figures | Moves from the old value to the new one; first load shows the value at once |
| `Button done` | Refill, HCP intent answers, follow-up and meeting forms | idle -> working -> done ✓ for 1.6 s |
| Nav indicator | Sidebar | One marker glides to the active page; collapse is instant |
| `useDrawIn` (`charts.tsx`) | Every chart frame | Bars grow from their baseline, lines draw, point groups fade, supply cells sweep, once when first in view (IntersectionObserver). Labels show final values from the start; later updates use the marks' own transitions |
| `Steps` with `seenKey`, `StreakCard` | Consultation steps, content lifecycle, streaks | Animate only when the value differs from what this viewer last saw (`preferences.lastSeen`); first sight and reloads are static |
| Verification code | Sign-up | Digit pop, a 4 px shake of the code row on a wrong code, green sweep on success (navigation waits at most 250 ms) |
| Landing (`pages/Landing.tsx`, lazy chunk with ScrollTrigger) | Hero, data-to-action strip, 8-step loop, section reveals | Hero builds a recommendation the way a reviewer reads it; the strip lights signal -> insight -> action -> review -> outcome; the loop highlights one step at a time only while on screen (mouse devices); one ScrollTrigger per section, `once`. Magnetic pull on the main sign-in buttons and a pointer spotlight on role cards, desktop mouse only |

CSS keeps the cheap pieces: `lift` hover, `animate-grow` / `animate-grow-up` (a "from" frame that hands over to the element's own scale), `animate-pop`, `animate-fade`, the skeleton opacity pulse, the engine progress bar (only while an operation runs), `.chart-hatch`, `.spotlight`.

Not used, on purpose: route exit transitions, animated sidebar width, staggering long lists, ScrollTrigger inside the app, count-up on load, celebrations or confetti, particles or canvas, pointer effects on dashboard cards, backdrop blur, pinning or scrubbing, theme cross-fades, shaking whole forms, counting inside chart labels, Recharts animation, character-by-character text, continuous pulses.

Reduced motion: every GSAP entry point checks `prefers-reduced-motion` (or uses `gsap.matchMedia`), so nothing moves and state changes remain visible as text and icons; the CSS override collapses the rest.

