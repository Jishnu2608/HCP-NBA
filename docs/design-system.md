# Design system

The web application uses one visual language for all six roles. Everything below lives in two files:

- `frontend/src/index.css`: design tokens (colours, shadows, layering), light and dark themes, base styles, focus ring, motion.
- `frontend/src/ui.tsx`: the components every page is built from.

Pages do not use raw colours or hand-made controls. If a page needs something new, add it to `ui.tsx`.

## Direction

The interface is aimed at enterprise healthcare and pharma users in the United States. It should feel calm and trustworthy, readable at a glance, and dense only where the data needs it. Avoid decoration that carries no information: no gradients on controls, no glass effects, no animation for its own sake.

## Colour

Colours are semantic tokens defined as CSS variables on `:root`, with dark values under `prefers-color-scheme: dark`. They are mapped into Tailwind as `bg-surface`, `text-ink-muted`, `bg-primary` and so on.

| Token | Use |
|---|---|
| `canvas`, `surface`, `subtle`, `sunken` | Page background (warm ivory), cards, insets, tracks |
| `line`, `line-strong` | Borders and dividers |
| `ink`, `ink-muted`, `ink-subtle` | Body text, secondary text, captions (all at least 4.5:1 on their surfaces) |
| `primary` (eucalyptus) | Primary buttons, active navigation, links, the brand mark |
| `sage` | Secondary chips and quiet highlights |
| `accent` (coral) | Used sparingly: the landing page's main call to action and "new" or attention dots |
| `nav` | Dark charcoal-eucalyptus sidebar and dark bands |
| `ok`, `warn`, `bad`, `info`, `neutral` | Status only. Each has `-soft` (background) and `-line` (border) variants |

Coral is never used for risk or errors, so it cannot be mistaken for a status.

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

## Typography

- Family: Public Sans (variable), self-hosted through `@fontsource-variable/public-sans`. No external font requests.
- Page title 28px semibold, tight tracking. Card title 15px semibold. Body 14–15px. Captions 13px. Section labels use sentence case, not uppercase.
- Numbers use tabular figures (`.tabular`).
- Headings use `text-wrap: balance`, paragraphs `text-wrap: pretty`.

## Shape, depth, spacing

- Radius: controls 8px, cards and panels 12px, badges and chips 6px. Avatars are rounded squares.
- Shadows are tinted to the palette (`shadow-card`, `shadow-raised`, `shadow-overlay`). Cards have a 1px border and a light shadow.
- Spacing follows the Tailwind 4px scale. Page gutter: 16px on phones, 24px on tablets, 32px on desktops. Content is capped at 1440px.
- Layering uses `--z-sticky`, `--z-nav`, `--z-drawer`, `--z-toast`. No other z-index values.

## Components (`ui.tsx`)

| Component | Notes |
|---|---|
| `PageHeader` | Title, optional back link, badges beside the title, subtitle, actions |
| `Card` | Titled panel; `flush` for edge-to-edge tables and lists |
| `Button`, `IconButton` | Variants: primary, accent, secondary, ghost, danger, quiet-danger. Sizes sm (36px), md (40px), lg (48px). Busy state with spinner |
| `Badge`, `StatusBadge`, `SegmentBadge`, `MlrBadge`, `ConsentBadge` | Status semantics above |
| `Stat`, `Meter` | Key figures and proportion bars |
| `DataTable` | Table on wide containers, stacked cards on narrow ones. Switches on the container's own width (container query), not the window's. Rows can be opened by click or keyboard |
| `Table` | Small fixed tables; scrolls sideways if it must |
| `Pagination`, `Segmented`, `Toolbar`, `Select`, `SearchInput` | List controls. `Segmented` scrolls sideways on phones instead of wrapping |
| `TextField`, `PasswordField`, `TextArea`, `Switch`, `FormField` | Label above the control, hint or error below, required marker, `aria-invalid` and `aria-describedby` wired up. Password fields have a show/hide toggle |
| `Alert`, `ErrorNote`, `ErrorState`, `EmptyState` | Designed empty, error and information states |
| `Skeleton`, `Loading`, `LoadingRows` | Skeleton loaders shaped like the content |
| `Timeline`, `TimelineItem` | Audit trails and histories |
| `Avatar`, `ChannelIcon`, `Truncate` | Initials, channel glyphs, long text with a native tooltip |

`toast.tsx` provides short success confirmations after an action. Errors stay inline next to the control that caused them.

## Layout

- Shell (`App.tsx`): dark sidebar from 1024px, collapsible to an icon rail (remembered per browser). Below 1024px the sidebar becomes a drawer opened from the top bar. The sidebar is grouped into Workspace, My account, Governance and Administration, and shows only routes the account's permissions allow (from `routes.tsx`).
- Top bar: demo date and an account menu showing the signed-in name, email, role and Sign out. A "Skip to content" link is the first focusable element.
- Detail pages (Patient 360, HCP 360, recommendation): main column plus a 360px side column from 1280px. Below that, the recommendation comes first, then the main content, then details.
- The recommendation page keeps the Decision panel in view while the rationale is read. On high zoom it scrolls within itself instead of being cut off.

## Accessibility

- Visible focus ring on every interactive element (`:focus-visible`), skip link, landmarks (`header`, `nav`, `main`, `aside`).
- Radio groups for role selection and filters, with arrow-key movement on the role cards.
- Verification code: six boxes that accept typing, Backspace, arrows, paste and one-time-code autofill.
- Touch targets at least 40px for primary controls. Motion is reduced to nearly zero under `prefers-reduced-motion`.
- Text contrast meets WCAG AA in both themes.

## Performance

Route pages are loaded on demand (`React.lazy` in `routes.tsx`). The charts library is fetched only with the dashboard. No image assets; the favicon is inline SVG.
