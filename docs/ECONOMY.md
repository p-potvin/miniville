# Miniville — the economy

Miniville had money but no *flows*: wages were minted, nothing was priced, no
business could fail, and wages only ever went up. `src/miniville/economy.py`
gives the town a cost of living and a business sector.

## Households

Money leaves a household in four ways:

| flow | when | amount |
| --- | --- | --- |
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

The money supply is a **closed loop** as of v0.9. It did not used to be: rent
was collected and destroyed, the levy's non-rebated share was destroyed (while
its own comment said it was "spent on the town's public services"), and
public-service payroll was minted by faking each public venue's revenue to
equal its payroll. Measured over 60 days on the live world that drained
**$734,088 — 7.8% of the money supply** — with rent alone destroying $974,470
and town-wide payroll ($1.67M) outrunning revenue ($1.41M). Left alone the
town deflated itself broke over a few simulated years.

Now there is a **town purse** (`town_account`, one row):

- **Rent is credited to the purse** instead of vanishing — the town is the
  landlord and its income is the rent roll.
- **The weekly levy** takes `BUSINESS_TAX_RATE` (5%) of each business's
  reserves; `LEVY_DIVIDEND_SHARE` (35%) goes straight back out as the **civic
  dividend** and the rest is credited to the purse.
- **Public-service payroll** (hospital, school, town hall, library, church,
  park — `PUBLIC_TAGS` / `kind='civic'`) is **debited from the purse**. If the
  purse cannot cover a payroll the shortfall is a **municipal deficit**: it
  emits a `town_deficit` event and is reported, rather than being minted
  silently every week.
- The purse keeps `PURSE_BUFFER_WEEKS` (6) of public payroll in hand; anything
  above that is residents' money sitting in a drawer, so it is added to the
  dividend. Without this valve the purse swallowed the rent roll forever and
  every wallet drained while the town account grew.
- **Wage dynamics.** `wage_index` (in `meta`) drifts down 0.5%/week when
  unemployment is above 12% and up when it is below 5%, clamped to 0.6–1.6.
  Every wage is `wage_cents * wage_index`, so wages no longer only go up.

After the change, over 90 days on the same world: total money **+$108,944**
instead of -$734,088, wallets flat ($9,147,529 → $9,168,716), purse bounded
near its buffer, zero municipal deficit.

## Estates, pensions and owners (v0.14)

Three more places where money stopped moving, found by soaking a fresh
synthetic town for a year (`scripts/synth_personas.py` → `init` → `soak.py`):

- **Estates.** The dead kept their wallets, so every death took its savings
  out of circulation — invisible to the money-supply series, which summed over
  the dead too. `mortality._settle_estate` passes the money to the surviving
  spouse, else the household (adults, then children, before orphans are
  rehomed), else the town purse. `estates_v1` returns what the dead already
  held to the purse.
- **Pensions.** Retirement ended a resident's wage and nothing replaced it:
  70 of the 105 broke households in the soak were entirely retired. Every
  resident 65+ without a job now draws `pension_week()` from the purse each
  week, before rent is due (`PENSION_BASE_WEEK_CENTS` × council policy
  `pension`, default 0.50). The purse's buffer covers pensions as well as the
  public payroll. A shortfall is a `town_deficit`, as with payroll.
- **Downsizing.** A household already in The Flats was "downsized to The
  Flats" again every fortnight (1,451 times in the soak year). There is
  nowhere cheaper to go, so it now stays put (and stays in arrears).

### Ownership and enterprise (`enterprise.py`)

A commercial business has a resident **owner** (`businesses.owner_id`):

- On first run, the senior hand (highest rank, then longest tenure) at each
  commercial venue becomes its proprietor (`enterprise_v1`).
- **Draws.** On the levy day each owner takes `DRAW_SHARE` (10%) of the
  reserve above `CUSHION_WEEKS` (4) of the venue's payroll.
- **Buyers.** A venue past its 21-day cooldown is bought by the resident who
  can best afford `BUY_CENTS` ($12k, which becomes its opening reserve). An
  original venue with no buyer reopens town-run as before. A founded venue
  with no buyer stays closed.
- **Founders.** Mid-week, while the town has fewer than one commercial venue
  per `ADULTS_PER_COMMERCIAL` (45) adults, there is a 10% chance that a
  resident with `CAPITAL_CENTS` ($20k) plus a cushion opens a venue in their
  district. They pick the concept (bakery, kitchen, bar, café, boutique,
  workshop, studio, stage) whose custom crowds its venues hardest, favouring
  their own trade. Crowding alone cannot bound foundings, because residents
  pick uniformly among matching venues, so a new café brings its own custom. The
  founder quits their post and becomes the venue's head, and the labour
  market staffs it. A founding fails like any other venue, and the owner
  loses the capital.
- An owner's business passes with their estate. Owning an open business is
  +4 influence.

Money only moves between wallet and till; nothing here mints it.

### Housing (`housing.py`)

The life lottery's move draw now asks what the household can afford. It
moves **up** to the best district whose rent is at most 1/2.5 of its weekly
income and 1/20 of its savings. It moves **one step down** when rent is over
1/1.4 of income with under six weeks saved. Otherwise it mostly stays put.
Before this, about 700 random moves a year ignored rent entirely.
`district_profile()` feeds the Economy tab's district table.

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
