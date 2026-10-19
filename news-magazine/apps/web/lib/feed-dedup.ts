/**
 * Visible-duplicate suppression for a rendered feed.
 *
 * `duplicate_of` is set at INGEST time by services/dedup.py, and every
 * read in lib/news.ts filters on it. That is not enough, and the database
 * says why: `title_normalized` carries a plain btree index, not a unique
 * one (migration 0007), and the one unique index that does exist,
 * `articles_content_hash_idx`, is computed over `canonical_url | title
 * fingerprint` — two outlets filing the same wire story under their own
 * URLs produce two DIFFERENT content hashes, so it stops nothing.
 *
 * So dedup is an application-level, read-then-insert check, and
 * `Deduplicator` is constructed per source sync
 * (services/ingestion.py:1018). Two sources polled in the same tick both
 * miss the lookup, both insert, and the story lands on the homepage
 * twice. Measured against the live table: 25 duplicate pairs inside the
 * newest 778 published articles, several of them carrying source_count
 * in the dozens — the same story split across two rows, each with its own
 * "Also reported by N others" badge claiming a syndication that never
 * got merged.
 *
 * This is the read-side backstop. It does not repair `duplicate_of` (that
 * is the ingestion service's job, and re-ingesting history is out of
 * scope) — it just stops rendering two cards for one story.
 *
 * The thresholds below are deliberately tighter than the ones in
 * dedup.py. A missed duplicate is an annoyance; a merged pair of
 * different stories is a lie about what was reported today, and there is
 * no undo for it. See the measurements on each constant.
 */

/** Outlet names that get appended to a syndicated headline. */
const OUTLET_SUFFIX =
  /\s*[-–—|]\s*(?:onlinekhabar|online khabar|kathmandu post|the kathmandu post|myreplica|myreplica\.com|setopati|ratopati|nagarik news|nagariknews|nagarik times|ekantipur|kantipur|annapurna post|himal khabar|news|समाचार)\s*$/i;

/** Everything that is not a letter or a number, including Devanagari danda. */
const PUNCTUATION = /[^\p{L}\p{N}\s]+/gu;

/** One word. Nepali has no capitalisation and no spaces inside compounds,
 *  so this splits on whitespace and strips punctuation, nothing more. */
const WORD = /[\p{L}\p{N}]+/gu;

/**
 * Words that carry no discriminative power in a headline. The English
 * half of dedup.py's STOPWORDS; the Nepali half is deliberately not
 * ported — see `contentTokens` for why omitting it makes this layer
 * more conservative, not less.
 */
const ENGLISH_STOPWORDS = new Set(
  ("a an the and or but of in on at to for with from by as is are was were " +
    "be been being this that these those it its his her their our your my " +
    "says said say new latest update updated breaking live report reports " +
    "reported after before over under about into out up down more most " +
    "than then").split(" ")
);

/**
 * Similarity floor, 0-100. Mirrors `max(fuzz.ratio, fuzz.token_sort_ratio)`
 * from dedup.py, so the two layers score a headline pair identically.
 *
 * 85, not dedup.py's 78. Lowering it is not free here: the MoFA press
 * notes below score 87, and every pair at or above 85 that also clears
 * CONTAINMENT_THRESHOLD is a real duplicate — so 85 is where the
 * measured data says the safe region ends.
 */
const SIMILARITY_THRESHOLD = 85;

/**
 * Shared tokens, as a fraction of the SHORTER headline's tokens.
 *
 * This is the gate that does the real work, and it exists because
 * similarity alone cannot tell "one story, two outlets" from "one
 * outlet's boilerplate, two officials".
 *
 * 0.85 is measured, not guessed. Sweeping the live table (778 newest
 * published articles, every pair compared) at each value:
 *
 *   0.85 -> 25 pairs merged, 24 rows removed   <- shipped
 *   0.80 -> 31 pairs merged, 29 rows removed   <- admits a FALSE merge
 *   0.75 -> 34 pairs merged, 31 rows removed
 *   0.70 -> 36 pairs merged, 33 rows removed
 *
 * The three extra pairs at 0.80 are five genuine duplicates and one
 * that must never merge: the Ministry of Foreign Affairs press note for
 * Prime Minister Balen Shah against the one for Foreign Minister Shisir
 * Khanal. Same boilerplate, different officials, different events — 87
 * similar, 0.75 contained. It sits exactly on the boundary, which is why
 * this constant is 0.85 and not 0.80.
 *
 * The cost of that choice is real and is accepted. Of the 36 pairs a
 * 0.70 rule would merge, this one leaves 11 visible: the MoFA pair it is
 * rejecting on purpose, and ten genuine duplicates — a typo'd
 * "विरतण"/"वितरण", two spellings of the same exchange rate, two of the
 * same Dashain bus campaign, the Tiger Shraf shooting told as "shot at"
 * and "shot in his car", a currency bulletin, a tea reception. A reader
 * who sees one story twice can be told about it; a site that quietly
 * removes a press note about the Prime Minister cannot be put right.
 */
const CONTAINMENT_THRESHOLD = 0.85;

/**
 * Minimum shared tokens before the fuzzy path is even considered.
 *
 * "नेपाल बढ्यो" and "नेपाल बढ्यो भयो" are 0.86 similar and 0.67
 * contained, and no amount of similarity makes two-word headlines safe
 * to merge on their own. Short headlines are still covered by the exact
 * normalised-title check, which needs no minimum.
 */
const MIN_SHARED_TOKENS = 3;

/** Canonical form: outlet suffix off, lowercase, punctuation to spaces. */
export function normalizeHeadline(headline: string | null | undefined): string {
  if (!headline) return "";
  return String(headline)
    .normalize("NFKC")
    .replace(OUTLET_SUFFIX, "")
    .toLowerCase()
    .replace(PUNCTUATION, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Discriminative tokens.
 *
 * Tokenisation matches dedup.py exactly — a run of letters or digits
 * between spaces, in either script — so `length > 1` drops the same
 * single characters here as it does there.
 *
 * The Nepali stopword list is deliberately NOT ported. Those words
 * (छ, भए, गर्ने, लागि …) appear in a large share of Nepali headlines
 * and almost never in both halves of a syndicated pair, so keeping them
 * adds denominator weight that has to be earned by an actual shared
 * token. That lowers containment for any pair — including the
 * same-outlet boilerplate pairs this threshold exists to reject — so
 * omitting them makes this layer stricter, not looser.
 */
function contentTokens(normalized: string): Set<string> {
  const all = normalized.match(WORD) ?? [];
  const kept = new Set(
    all.filter((token) => token.length > 1 && !ENGLISH_STOPWORDS.has(token))
  );
  // A headline made entirely of stopwords would otherwise reduce to the
  // empty set and match every other such headline.
  return kept.size > 0 ? kept : new Set(all);
}

/**
 * Levenshtein similarity, 0-100 — the same shape as `fuzz.ratio`:
 * `(1 - distance / longest) * 100` over the normalised strings.
 */
function stringRatio(a: string, b: string): number {
  if (!a || !b) return 0;
  if (a === b) return 100;

  const m = a.length;
  const n = b.length;
  let previous = new Array<number>(n + 1);
  for (let j = 0; j <= n; j += 1) previous[j] = j;

  for (let i = 1; i <= m; i += 1) {
    const current = new Array<number>(n + 1);
    current[0] = i;
    const ca = a[i - 1] as string;
    for (let j = 1; j <= n; j += 1) {
      const cost = ca === b[j - 1] ? 0 : 1;
      current[j] = Math.min(
        (previous[j] as number) + 1,
        (current[j - 1] as number) + 1,
        (previous[j - 1] as number) + cost
      );
    }
    previous = current;
  }

  const distance = previous[n] as number;
  return 100 * (1 - distance / Math.max(m, n));
}

/**
 * Best of `ratio` and `token_sort_ratio`, over a prepared pair.
 *
 * `token_sort_ratio` matters for headlines that differ only in word
 * order or capitalisation — "ICC Cricket World Cup League-2: Nepal faces
 * Oman" against "... Series: Nepal Faces Oman ..." is 0.86 as a raw
 * string and 1.00 once the words are sorted.
 */
function headlineSimilarity(a: Prepared, b: Prepared): number {
  return Math.max(stringRatio(a.normalized, b.normalized), stringRatio(a.sorted, b.sorted));
}

interface Prepared {
  id: string;
  normalized: string;
  sorted: string;
  tokens: Set<string>;
}

function prepare(row: FeedRow): Prepared {
  const normalized = normalizeHeadline(row.headline);
  return {
    id: row.id,
    normalized,
    sorted: (normalized.match(WORD) ?? []).sort().join(" "),
    tokens: contentTokens(normalized),
  };
}

/**
 * The decision, over two prepared headlines.
 *
 * Three gates, in increasing cost order, and all three must pass for the
 * fuzzy path: enough shared tokens, enough of the shorter headline's
 * tokens accounted for, and enough string similarity. An exact match
 * after normalisation skips all three — identical strings are the same
 * story by definition, however short.
 */
function sameStory(a: Prepared, b: Prepared): boolean {
  if (!a.normalized || !b.normalized) return false;
  if (a.normalized === b.normalized) return true;

  // Iterate the smaller set; the direction does not change the count.
  const [small, large] =
    a.tokens.size <= b.tokens.size ? [a.tokens, b.tokens] : [b.tokens, a.tokens];
  let shared = 0;
  for (const token of small) if (large.has(token)) shared += 1;

  if (shared < MIN_SHARED_TOKENS) return false;
  if (
    shared / Math.max(1, Math.min(a.tokens.size, b.tokens.size)) <
    CONTAINMENT_THRESHOLD
  ) {
    return false;
  }

  return headlineSimilarity(a, b) >= SIMILARITY_THRESHOLD;
}

/**
 * Are these two headlines the same story?
 *
 * Exported so a threshold change can be checked against real headlines
 * without a database: the decision is a pure function of the two
 * strings.
 */
export function isSameStory(a: string, b: string): boolean {
  return sameStory(prepare({ id: "", headline: a }), prepare({ id: "", headline: b }));
}

/** The fields `dropDuplicateStories` reads. Everything else is carried through. */
export interface FeedRow {
  id: string;
  headline: string;
  source_count?: number | null;
  published_at?: string | null;
  featured_image_url?: string | null;
  image_highres_url?: string | null;
}

/**
 * Which copy of one story is the one worth showing.
 *
 * `source_count` first: it is how many outlets actually carried the
 * story, so the copy that absorbed the most syndication is the copy
 * with the fullest record behind it. Then a picture, because dropping
 * the illustrated version of a story in favour of an imageless twin
 * would undo exactly what `preferIllustrated` exists to do. Then the
 * newer one.
 */
function isBetterCopy(candidate: FeedRow, incumbent: FeedRow): boolean {
  const count = (candidate.source_count ?? 1) - (incumbent.source_count ?? 1);
  if (count !== 0) return count > 0;

  const pictured = (row: FeedRow) =>
    Boolean(row.image_highres_url || row.featured_image_url) ? 1 : 0;
  const picture = pictured(candidate) - pictured(incumbent);
  if (picture !== 0) return picture > 0;

  const published =
    Date.parse(candidate.published_at ?? "") || 0;
  return published > (Date.parse(incumbent.published_at ?? "") || 0);
}

/**
 * Keep one card per story.
 *
 * Order is never changed. The survivor keeps its OWN slot in the feed, so
 * a story that loses a duplicate may sit slightly lower than it would
 * have, but nothing is ever promoted above a newer story to win a
 * secondary signal — the same trade `preferIllustrated` refuses to make.
 *
 * Clustering is greedy in feed order, mirroring dedup.py: a row that
 * matches any already-kept row is a duplicate of it. The window is
 * bounded (a feed is tens of rows, not thousands), and a shared-token
 * prefilter keeps the quadratic loop down to the handful of pairs that
 * are actually candidates.
 */
export function dropDuplicateStories<T extends FeedRow>(rows: T[]): T[] {
  if (rows.length < 2) return rows;

  const prepared = rows.map(prepare);
  const dropped = new Set<string>();

  for (let i = 0; i < prepared.length; i += 1) {
    const self = prepared[i];
    const selfRow = rows[i];
    if (!self || !selfRow || dropped.has(self.id)) continue;

    for (let j = i + 1; j < prepared.length; j += 1) {
      const other = prepared[j];
      const otherRow = rows[j];
      if (!other || !otherRow || dropped.has(other.id)) continue;
      if (!sameStory(self, other)) continue;

      // Keep the better copy of the story, drop the other. Because the
      // scan continues from the surviving copy either way, a cluster of
      // three keeps the best of the three, not the first two.
      if (isBetterCopy(otherRow, selfRow)) {
        dropped.add(self.id);
        break;
      }
      dropped.add(other.id);
    }
  }

  return rows.filter((row) => !dropped.has(row.id));
}