// TEMPORARY: compare CONTAINMENT 0.85 vs 0.80 on live data.
import fs from "node:fs";
import path from "node:path";
import { createClient } from "@supabase/supabase-js";

const env = {};
for (const line of fs.readFileSync(path.resolve(".env.local"), "utf8").split(/\r?\n/)) {
  const m = line.match(/^([A-Z0-9_]+)\s*=\s*(.*)$/);
  if (m) env[m[1]] = m[2].trim();
}
const supabase = createClient(env.NEXT_PUBLIC_SUPABASE_URL, env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });
const { data } = await supabase.from("articles")
  .select("id, headline, source_count, published_at, source_name_snapshot")
  .eq("status", "published").is("duplicate_of", null)
  .order("published_at", { ascending: false, nullsFirst: false }).limit(1200);
const rows = data ?? [];
console.log("rows:", rows.length);

async function load(thr) {
  fs.copyFileSync("tmp-verify/feed-dedup.js", "tmp-verify/fd.js");
  let src = fs.readFileSync("tmp-verify/fd.js", "utf8");
  src = src.replace("CONTAINMENT_THRESHOLD = 0.85", `CONTAINMENT_THRESHOLD = ${thr}`);
  fs.writeFileSync("tmp-verify/fd.js", src);
  const m = await import(`./tmp-verify/fd.js?v=${thr}`);
  return m;
}

const key = (a, b) => [a.id, b.id].sort().join("|");
const sets = {};
for (const thr of [0.85, 0.82, 0.80, 0.78, 0.75]) {
  const m = await load(thr);
  const pairs = [];
  for (let i = 0; i < rows.length; i++)
    for (let j = i + 1; j < rows.length; j++)
      if (m.isSameStory(rows[i].headline, rows[j].headline)) pairs.push(key(rows[i], rows[j]));
  sets[thr] = new Set(pairs);
  const dropped = m.dropDuplicateStories(rows).length;
  console.log(`containment ${thr}: ${pairs.length} pairs, ${rows.length - dropped}/${rows.length} rows would be removed`);
}

const lookup = new Map();
for (const r of rows) lookup.set(r.id, r);
console.log(`\n--- pairs added by going 0.85 -> 0.80 ---`);
for (const p of sets[0.80]) if (!sets[0.85].has(p)) {
  const [a, b] = p.split("|");
  console.log(`  A [${lookup.get(a).source_name_snapshot}] ${lookup.get(a).headline}`);
  console.log(`  B [${lookup.get(b).source_name_snapshot}] ${lookup.get(b).headline}\n`);
}
console.log(`--- pairs added by going 0.80 -> 0.75 ---`);
for (const p of sets[0.75]) if (!sets[0.80].has(p)) {
  const [a, b] = p.split("|");
  console.log(`  A [${lookup.get(a).source_name_snapshot}] ${lookup.get(a).headline}`);
  console.log(`  B [${lookup.get(b).source_name_snapshot}] ${lookup.get(b).headline}\n`);
}