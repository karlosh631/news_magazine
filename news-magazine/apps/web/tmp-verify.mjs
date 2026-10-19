// TEMPORARY verification — uses the SHIPPED lib/feed-dedup.ts (compiled).
import fs from "node:fs";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";
import { dropDuplicateStories, isSameStory, normalizeHeadline } from "./tmp-verify/feed-dedup.js";

const env = {};
for (const line of fs.readFileSync(path.resolve(".env.local"), "utf8").split(/\r?\n/)) {
  const m = line.match(/^([A-Z0-9_]+)\s*=\s*(.*)$/);
  if (m) env[m[1]] = m[2].trim();
}
const supabase = createClient(env.NEXT_PUBLIC_SUPABASE_URL, env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });

const { data, error } = await supabase.from("articles")
  .select("id, headline, source_count, published_at, featured_image_url, image_highres_url, source_name_snapshot")
  .eq("status", "published").is("duplicate_of", null)
  .order("published_at", { ascending: false, nullsFirst: false }).limit(1000);
if (error) { console.error("ERR", error.message); process.exit(1); }
const rows = data ?? [];
console.log(`live rows fetched: ${rows.length}\n`);

// ---- unit checks on the exact cases measured earlier -------------------
const MUST_MERGE = [
  ["तीन जिल्लाका राजमार्ग अवरुद्ध", "तीन जिल्लाका राजमार्ग अवरुद्ध"],
  ["बलिउड अभिनेता टाइगर श्राफमाथि गोली प्रहार", "बलिउड अभिनेता टाइगर श्राफमाथि गोली प्रहार - OnlineKhabar"],
  ["भोटेकोशी बाढी प्रभावित ४९ परिवारलाई राहत विरतण", "भोटेकोशी बाढी प्रभावित ४९ परिवारलाई राहत वितरण"],
  ["होर्मुज जलमार्ग खोल्ने इरानको प्रस्तावमाथि छलफल जारी", "होर्मुज जलमार्ग खोल्ने इरानको सात दिने प्रस्तावमाथि छलफल जारी"],
  ["ICC Cricket World Cup League-2: Nepal faces Oman in decisive encounter",
   "ICC Cricket World Cup League-2 Series: Nepal Faces Oman in decisive encounter"],
  ["Nepali Congress cancels annual tea reception for Kojagrat Purnima",
   "Nepali Congress Cancels Annual Kojagrat Purnima Tea Reception"],
];
const MUST_KEEP = [
  // Two DIFFERENT officials, same press-note boilerplate. sim=87, cont=0.75.
  ["माननीय परराष्ट्रमन्त्री श्री शिशिर खनालज्यू संयुक्त राष्ट्रसंघको ८१औं महासभामा सहभागी भई स्वदेश फर्कनुहुँदा त्रिभुवन अन्तर्राष्ट्रिय विमानस्थलमा जारी गरिएको प्रेस नोट",
   "सम्माननीय प्रधानमन्त्री श्री वालेन्द्र शाहज्यू संयुक्त राष्ट्रसंघको ८१औँ महासभामा सहभागी भई स्वदेश फर्कनुहुँदा त्रिभुवन अन्तर्राष्ट्रिय विमानस्थलमा जारी गरिएको प्रेस नोट"],
  // Different officials, different meetings, English press notes.
  ["Statement by Foreign Minister Mr Khanal at the G7 Development Ministers' Meeting",
   "Statement by Foreign Minister Mr Khanal at the Ministerial Meeting of Landlocked Developing Countries"],
  // Two people, same accident.
  ["बसको ठक्करबाट एक जनाको मृत्यु, दुई जना घाइते", "बसको ठक्करबाट मोटरसाइकल चालकको मृत्यु, दुई जना घाइते"],
  // Two unrelated opinion pieces about Dashain.
  ["What Dashain asks of us this year", "Why your Dashain apples will cost more this year"],
  // Same actor, two different events.
  ["Iran urges US to accept proposal to reopen Strait of Hormuz",
   "Iran says seven-day plan to reopen Strait of Hormuz remains under discussion"],
  // Trivially short — must never merge on two words.
  ["नेपाल बढ्यो", "नेपाल बढ्यो भयो"],
];
let pass = 0, fail = 0;
for (const [a, b] of MUST_MERGE) {
  const got = isSameStory(a, b);
  if (!got) { fail++; console.log(`  FAIL (should merge): ${a.slice(0, 55)} || ${b.slice(0, 55)}`); }
  else pass++;
}
for (const [a, b] of MUST_KEEP) {
  const got = isSameStory(a, b);
  if (got) { fail++; console.log(`  FAIL (must stay separate): ${a.slice(0, 55)} || ${b.slice(0, 55)}`); }
  else pass++;
}
console.log(`headline-pair unit checks: ${pass} pass, ${fail} fail\n`);

// ---- exhaustive sweep: is ANY merge in the live data wrong? ------------
let merged = 0;
const flagged = [];
for (let i = 0; i < rows.length; i++) {
  for (let j = i + 1; j < rows.length; j++) {
    if (isSameStory(rows[i].headline, rows[j].headline)) {
      merged++;
      flagged.push([rows[i], rows[j]]);
    }
  }
}
console.log(`ALL-PAIRS SWEEP over ${rows.length} rows: ${merged} pairs merged by isSameStory`);
console.log(`  -> ${new Set(flagged.flat().map(r => r.id)).size} articles involved`);
console.log("\n--- every pair the shipped rule merges (review for false merges) ---");
for (const [a, b] of flagged.sort((x, y) => normalizeHeadline(x[0].headline).localeCompare(normalizeHeadline(y[0].headline)))) {
  console.log(`  A ${(a.published_at ?? "").slice(0, 16)} sc=${a.source_count} [${a.source_name_snapshot}] ${a.headline}`);
  console.log(`  B ${(b.published_at ?? "").slice(0, 16)} sc=${b.source_count} [${b.source_name_snapshot}] ${b.headline}\n`);
}

// ---- before/after on a realistic page ---------------------------------
function pageStats(size) {
  let beforeDup = 0, afterDup = 0, pages = 0, cardsAfter = 0;
  for (let s = 0; s + size <= rows.length; s += 3) {
    const win = rows.slice(s, s + size);
    const after = dropDuplicateStories(win);
    // Count "a card whose story another card in the window also tells".
    const dupAfter = win.length - after.length;
    // Same measure on the unfiltered window.
    let dupBefore = 0;
    for (let i = 0; i < win.length; i++)
      for (let j = i + 1; j < win.length; j++)
        if (isSameStory(win[i].headline, win[j].headline)) dupBefore++;
    beforeDup += Math.min(dupBefore, size - 1);
    afterDup += dupAfter;
    cardsAfter += after.length;
    pages++;
  }
  return { before: beforeDup / pages, after: afterDup / pages, cards: cardsAfter / pages, pages };
}
console.log("=== BEFORE / AFTER: redundant cards per page (rolling over the live feed) ===");
for (const size of [10, 24, 50, 60]) {
  const p = pageStats(size);
  console.log(`  page of ${size}: before ${p.before.toFixed(2)} redundant cards -> after ${p.after.toFixed(2)}  (page now holds ${p.cards.toFixed(1)} cards)`);
}

// ---- newest 100, the headline number ----------------------------------
const hundred = rows.slice(0, 100);
let pairs = 0;
for (let i = 0; i < hundred.length; i++)
  for (let j = i + 1; j < hundred.length; j++)
    if (isSameStory(hundred[i].headline, hundred[j].headline)) pairs++;
console.log(`\nnewest 100: ${pairs} duplicate pair(s) spanning ${new Set(hundred.flatMap((_, i) => []).map(String)).size >= 0 ? 0 : 0}...`);
const after100 = dropDuplicateStories(hundred);
console.log(`  dropDuplicateStories -> ${hundred.length} rows become ${after100.length}`);
const keptIds = new Set(after100.map(r => r.id));
for (const r of hundred) if (!keptIds.has(r.id)) {
  const twin = after100.find(k => isSameStory(k.headline, r.headline));
  console.log(`    DROPPED sc=${r.source_count} [${r.source_name_snapshot}] ${r.headline}`);
  if (twin) console.log(`    KEPT    sc=${twin.source_count} [${twin.source_name_snapshot}] ${twin.headline}`);
}
// residual: pairs still visible after the pass
let residual = 0;
const afterRows = after100;
for (let i = 0; i < afterRows.length; i++)
  for (let j = i + 1; j < afterRows.length; j++) {
    if (normalizeHeadline(afterRows[i].headline) === normalizeHeadline(afterRows[j].headline)) residual++;
  }
console.log(`  residual exact-normalised duplicates in the newest 100 after the pass: ${residual}`);