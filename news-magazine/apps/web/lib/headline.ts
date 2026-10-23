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
