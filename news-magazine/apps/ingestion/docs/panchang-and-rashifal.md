# The panchang and the rashifal — what the frontend reads

Two tables, written by the ingestion service, read by the site with the
**anon** key. Both are public-read and nothing else: the anon key can
`select` and can do nothing else.

Migrations: `supabase/migrations/0022_daily_panchang.sql` and
`supabase/migrations/0025_daily_rashifal.sql`.

The two tables are written by ONE hourly job (`ingest-panchang` in
`apps/ingestion/app/workers/scheduler.py`) and share ONE key:
`panchang_date`, the **Gregorian** day the row is for. There is no
separate "today" column — ask for today's date, or ask for the newest
row and use its `panchang_date`.

---

## 1. `daily_panchang` — the almanac (one row per day)

The header line: today's Bikram Sambat date, the tithi, the nakshatra,
sunrise and sunset, and the hours the panchang calls auspicious and
inauspicious.

| column | type | what it is |
|---|---|---|
| `panchang_date` | `date` **PK** | the Gregorian day this row is for |
| `bikram_sambat_year` | `integer` | e.g. `2083` |
| `bikram_sambat_month` | `integer` | 1–12, **1-based** (Baishakh = 1) |
| `bikram_sambat_day` | `integer` | 1–31 |
| `bikram_sambat_label` | `text` | the date as the site prints it: `कार्तिक ५, २०८३` |
| `weekday` | `text` | `Thursday` |
| `weekday_np` | `text` | `बिहिवार` |
| `tithi` | `text` | the bare name: `एकादशी` |
| `tithi_full` | `text` | with its lunar month and paksha: `असोज शुक्ल एकादशी` |
| `paksha` | `text` | `शुक्ल` or `कृष्ण` |
| `nakshatra` | `text` | `शतभिषा` |
| `yoga` | `text` | `वृद्धि` |
| `karana` | `text` | `विष्टि/भद्रा` |
| `moon_phase` | `text` | `बढ्दो गिब्बस` |
| `sunrise` | `time` | Nepal wall clock, `06:07` |
| `sunset` | `time` | Nepal wall clock, `17:28` |
| `sunrise_at` | `timestamptz` | the same moment as an instant: `2026-10-22T06:07:00+05:45` |
| `sunset_at` | `timestamptz` | the same, for sunset |
| `day_length_minutes` | `integer` | `681` (11 h 21 m) |
| `auspicious_times` | `jsonb` | the good hours, see below |
| `inauspicious_times` | `jsonb` | the bad hours, see below |
| `source_url` | `text` | where the row came from |
| `fetched_at` | `timestamptz` | when we last confirmed it |
| `updated_at` | `timestamptz` | maintained by a trigger |

**`tithi_full` names the LUNAR month, and it is routinely the month
BEFORE the solar one.** On 2026-10-22 the tithi is `असोज शुक्ल
एकादशी` while the Bikram Sambat date is Kartik. That is the panchang
being correct, not a bug — do not "fix" it by substituting the solar
month.

### The time windows

`auspicious_times` and `inauspicious_times` are arrays of:

```json
{
  "khanda": "प्रथम",
  "period": "day",
  "from": "06:07",
  "to": "07:32",
  "result": "शुभ",
  "start_minute": 367,
  "end_minute": 452
}
```

- `from` / `to` are `HH:MM` **Nepal time**, for display.
- `start_minute` / `end_minute` are minutes from local midnight, and
  **the night's numbers run past 1440** — 05:14 the next morning is
  `1754`. That is what keeps the night sequence monotonic and stops
  the frontend having to guess which side of midnight a time is on.
- `period` is `"day"` (sunrise → sunset) or `"night"` (sunset → the
  next sunrise). The night **crosses midnight**.
- `result` is the फल the site published: `शुभ`, `लाभ`, `अमृत` (good)
  or `रोग`, `उद्वेग`, `चर`, `काल` (bad). The split into the two
  columns is by that label, so `auspicious_times` and
  `inauspicious_times` together are every window, with nothing lost.
- The `khanda` (खण्ड) is the eighth of the day or the night the
  window falls in: `प्रथम` … `अष्टम`.

There is no "lucky direction" or "muhurat" column: the source does not
publish one.

### Querying it

```ts
const { data } = await supabase
  .from("daily_panchang")
  .select("*")
  .order("panchang_date", { ascending: false })
  .limit(1)
  .maybeSingle();
```

One row a day, so the table stays tiny (365 rows a year). A
`.eq("panchang_date", today)` is exact and cheap; the primary key is
the date, so it is an index lookup.

---

## 2. `daily_rashifal` — the twelve horoscopes (twelve rows a day)

The category page: the twelve signs with their advice.

| column | type | what it is |
|---|---|---|
| `panchang_date` | `date` | the Gregorian day — **part of the primary key** |
| `sign` | `smallint` | **1–12**, the position: 1 = मेष … 12 = मीन. **part of the primary key** |
| `sign_slug` | `text` | the site's own id: `mesh`, `brish`, `mithun`, `karkat`, `singha`, `kanya`, `tula`, `brischik`, `dhanu`, `makar`, `kumbha`, `meen` |
| `sign_name` | `text` | `Aries` |
| `sign_name_ne` | `text` | `मेष` |
| `sign_emoji` | `text` | `♈` |
| `initials` | `text` | the letter initials the site prints under the name: `चु, चे, चो, ला, लि, लु, ले, लो, अ` |
| `advice` | `text` | today's advice, **verbatim** |
| `lucky_colour` | `text` | `गुलाबी` — or `null` if the sentence was not recognised |
| `lucky_number` | `text` | `७` — **in Nepali numerals**, as the site prints it |
| `source_url` | `text` | where the row came from |
| `fetched_at` | `timestamptz` | when we last confirmed it |
| `updated_at` | `timestamptz` | maintained by a trigger |

**Two things about `advice` you need to know:**

1. **It ends with the lucky sentence.** The site writes the advice and
   then, in the same paragraph, "आजको शुभ रंग गुलाबी हो भने शुभ अंक ७
   रहेको छ।" — and that sentence is where `lucky_colour` and
   `lucky_number` come from. So if you render `advice` **and** show
   the colour and the number as badges, you will show that sentence
   twice. Either render `advice` as it stands and skip the badges, or
   strip the trailing sentence. The parser leaves it in on purpose: if
   the site ever rewords it, the words are still in the row.
2. `advice` may be `null` (the site published none for that sign) and
   so may `lucky_colour` / `lucky_number` (the sentence was not the
   one the parser knows). `null` and `""` mean different things and
   the columns keep them apart.

**There is no `advice_en` column.** The site publishes no English
advice: the `descEn` field is empty on the Nepali page *and* on
`/en/rashifal`. If it ever starts publishing one, that is a new
column and a new migration.

### Querying it

```ts
const { data } = await supabase
  .from("daily_rashifal")
  .select("*")
  .eq("panchang_date", today)
  .order("sign", { ascending: true });
```

`order("sign")` is the zodiac order. `sign` is also the second half of
the primary key, so the sort costs nothing.

To get a sign's history (every Scorpio reading the site has
published), use the `daily_rashifal_sign_idx` index:

```ts
const { data } = await supabase
  .from("daily_rashifal")
  .select("*")
  .eq("sign", 8)
  .order("panchang_date", { ascending: false });
```

### The twelve signs, in order

| `sign` | `sign_slug` | `sign_name` | `sign_name_ne` |
|---|---|---|---|
| 1 | `mesh` | Aries | मेष |
| 2 | `brish` | Taurus | वृष |
| 3 | `mithun` | Gemini | मिथुन |
| 4 | `karkat` | Cancer | कर्कट |
| 5 | `singha` | Leo | सिंह |
| 6 | `kanya` | Virgo | कन्या |
| 7 | `tula` | Libra | तुला |
| 8 | `brischik` | Scorpio | वृश्चिक |
| 9 | `dhanu` | Sagittarius | धनु |
| 10 | `makar` | Capricorn | मकर |
| 11 | `kumbha` | Aquarius | कुम्भ |
| 12 | `meen` | Pisces | मीन |

---

## 3. Joining the two

Both tables are keyed by `panchang_date`, so a page that wants both
asks for both and joins on the date in TypeScript. There is no
foreign key between them, on purpose: the panchang and the horoscopes
are written by the same pass but are stored independently, and one of
them failing must not be able to stop the other.

## 4. Before the first rows exist

Until the migrations are applied and the job has run, both tables are
empty. **An empty result is the correct state for an unlaunched
feature, not an error** — render the empty state and do not throw.
The ingestion service logs a warning naming the migration it is
waiting for.

The job runs hourly (`PANCHANG_POLL_SECONDS`, default 3600). It
resolves today's date from a small page on every run and downloads the
large per-day page only when the day has rolled over, so a row can be
up to an hour old — and a day can be several hours old if the worker
was asleep.

## 5. What the source is

Everything comes from **hamropatro.com**, whose `robots.txt` allows
`/rashifal` and `/date/`. The panchang is the one the Nepal Panchanga
Nirnayak Samiti computes, and the site credits that committee at the
foot of the page. The rashifal is the site's own horoscope text.
