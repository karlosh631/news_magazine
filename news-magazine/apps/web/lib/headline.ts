/**
 * A headline that says the same thing twice, said once.
 *
 * Some publishers put the headline in their markup twice — once as the
 * visible heading and once in a second element the desktop and the
 * phone each use — and the listing parser reads the container rather
 * than the heading. The stored headline then reads:
 *
 *     Tanker with Russian diesel heads to New York under Trump deal
 *     Tanker with Russian diesel heads to New York under Trump deal
 *
 * which is what the reader saw in the search dropdown: the outlet's
 * headline with the outlet's own headline appended to it.
 *
 * The rule is deliberately strict, and it is the same rule the
 * ingestion side applies on the way into the database
 * (`collapse_repeated_headline` in app/services/sanitizer.py). A
 * headline is collapsed only when it is exactly two copies of the same
 * string, or when everything after the first copy is that copy again.
 * A headline that merely mentions itself ("Man arrested after man
 * arrested in theft") is news, not a bug, and is left alone.
 *
 * The web copy exists for the rows that were stored BEFORE the
 * ingestion learned the rule: they are in the database, and the
 * reader should not have to see them twice.
 */

export function collapseRepeatedHeadline(text: string | null | undefined): string {
  const value = (text ?? "").trim();
  if (value.length < 20) return value;

  // Try every possible length of the first copy. The rest of the
  // headline must be nothing BUT copies of it - one copy, two copies,
  // whatever - or this is not a repeated headline.
  for (let size = Math.floor(value.length / 2); size >= 12; size--) {
    const head = value.slice(0, size).trim();
    if (!head) continue;

    let rest = value.slice(size).trim();
    if (!rest) continue;

    let leftover = rest;
    while (leftover.startsWith(head)) {
      leftover = leftover.slice(head.length).trim();
    }
    if (leftover === "") return head;
  }

  return value;
}

/**
 * The headline, exactly as the outlet published it.
 *
 * The listing card is the publisher's own summary of the story, and
 * on a client-rendered listing that summary is the whole card. The
 * card's furniture - the outlet's domain, the time since publication,
 * the date and the time - is not part of the headline, and a reader
 * was being shown all of it:
 *
 *     gorkhapatraonline.com . 55 minutes ago Budhanilkanada's one
 *     death ...
 *
 * The rule strips that furniture from the FRONT of the headline and
 * leaves the rest exactly as the outlet wrote it.
 *
 * It is the same rule the ingestion side applies on the way into the
 * database (`sanitize_headline` in app/services/sanitizer.py), so the
 * rows that are already stored and the rows that arrive now are read
 * the same way.
 */
export function cleanHeadline(text: string | null | undefined): string {
  const value = collapseRepeatedHeadline(text);
  if (!value) return value;

  // A domain, a bullet, and the time since publication.
  const withRelative = value.match(
    /^[\w-]+(?:\.[\w-]+)*\.(?:com\.np|com|net|org|np|gov|edu|io)\s*[•·|]?\s*(?:[\d\u0966-\u096f]+\s*(?:minutes?|mins?|hours?|hrs?)\s+ago|[\d०-९]+\s*मिनेट\s*अघि|[\d०-९]+\s*घंटा\s*अघि)\s*[•·|]?\s*/i
  );
  if (withRelative) {
    const rest = value.slice(withRelative[0].length).trim();
    if (rest) return rest;
  }

  // A domain, a bullet, and the absolute date and time.
  const withDate = value.match(
    /^[\w-]+(?:\.[\w-]+)*\.(?:com\.np|com|net|org|np|gov|edu|io)\s*[•·|]?\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+\w{3,}\.?\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\s+(?:GMT|UTC)\s+\d{4}\s*[•·|]?\s*/i
  );
  if (withDate) {
    const rest = value.slice(withDate[0].length).trim();
    if (rest) return rest;
  }

  return value;
}
