# Miniville — the economy

Miniville had money but no *flows*: wages were minted, nothing was priced, no
business could fail, and wages only ever went up. `src/miniville/economy.py`
gives the town a cost of living and a business sector.

## Households

Money leaves a household in four ways:

| flow | when | amount |
|---|---|---|
| rent | weekly, `day % 7 == 0` | by district, $290–$460/wk, split across the adults |
| groceries | once a day, tick 14 | $7.50, paid to the town grocer |
| meals out | each `eat_out` tick | $10–$25 depending on the venue's tags |
| shopping / leisure | each `shopping` tick, paid leisure venues | $7–$60 |

Discretionary spending is capped at what a resident actually has, so nobody
goes below zero buying a coffee. Rent is different: a household that cannot
cover it goes into arrears (`rent_arrears`), and **two missed payments move the
household to The Flats** — the cheapest district — with a `downsize` event.

Wages arrive at `shift_end`, but only for staff who actually worked that day
(`plans` has a `work` row). `jobs.work_days` has always said Mon-Fri; the wage
pass used to ignore it and pay every job seven days a week.

## Businesses

Every non-home venue has a row in `businesses`:

- **Customer spending is credited** to the venue that served it (`revenue_today`),
  and its foot traffic is counted (`traffic_today` → `ema_traffic`).
- **Wages paid to its staff are debited** (`payroll_today`).
- **Public-service venues** (hospital, school, town hall, gazette, church —
  `PUBLIC_TAGS`) are funded by the town: their payroll is written off, so they
  break even and never fail.
- **Commercial venues** live on customer revenue. A venue that bleeds past
  `FAIL_THRESHOLD_CENTS` (−$60,000) **closes**: its jobs are deleted, its staff
  are laid off, and it stops being a destination (schedules and hiring both
  skip closed venues). It reopens under new management after 21 days.
- A venue in the red **raises its prices** (`price_index`, up to 1.6×); a
  comfortable one is undercut by the competition next door and drifts back down
  (floor 0.85×). Prices are a real lever: they change what residents pay.

In a healthy town failures are rare — they need a venue to lose its customers,
which is why the mechanism is exercised by tests and by harsher calibrations
rather than by the live world.

## The town's books

Wages are *minted* and rent is *destroyed*, so the money supply is not closed.
Two mechanisms keep it from running away:

- **The weekly levy.** Every Monday the town takes `BUSINESS_TAX_RATE` (5%) of
  each business's reserves. Most of it is what the town runs on and leaves
  circulation; `LEVY_DIVIDEND_SHARE` (35%) is handed back to every resident as
  a **civic dividend**. Without the levy, money paid to a shop or tavern is gone
  for good and the town slowly bleeds dry.
- **Wage dynamics.** `wage_index` (in `meta`) drifts down 0.5%/week when
  unemployment is above 12% and up when it is below 5%, clamped to 0.6–1.6.
  Every wage is `wage_cents * wage_index`, so wages no longer only go up.

## Reporting

- `economy_days` is a daily time series: revenue, payroll, rent, spending,
  money supply, unemployment, businesses open/closed, wage index.
- `economy_stats()` / `economy_line()` feed `cli status`, the chronicle's
  "Economy" section, the Gazette's back page, and the observer UI's Economy tab.
- CLI: `miniville economy [--days N]`.
- API: `GET /api/economy` → `{stats, businesses, series}`.

## Calibration

The scales were picked so a working household roughly covers rent, food and
errands rather than inflating. Verified over 120 simulated days on a copy of the
live world: money supply flat (±$1k/day on ~$9M), median wallet drifting down
~$20/day, unemployment 12–19%, a handful of households in arrears and
downsizing, all businesses solvent, and the town's moods unchanged.

The `economy_v1` / `economy_v2` migrations in `db.py` rescale an older world's
wages and balances (×2.0, then ×1.4) so existing towns keep their relative
position. Both are flagged and idempotent.
