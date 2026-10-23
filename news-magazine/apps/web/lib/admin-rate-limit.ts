/**
 * How fast somebody may try to sign in.
 *
 * WHAT A LOGIN FORM IS WITHOUT THIS
 * ---------------------------------
 * A password is a secret, and the only thing standing between an
 * attacker and a guess is how many guesses they get to make. Without
 * a limit, the sign-in form IS the attack: a script can make
 * thousands of guesses a minute, and the form answers every one of
 * them.
 *
 * THE TWO LIMITS
 * --------------
 * Per ACCOUNT, by the email address that was typed. This is the one
 * that matters, and it is the one that cannot be evaded by changing
 * where the request comes from.
 *
 * Per ADDRESS, by the IP the request came from. This is the blunt
 * one: it stops a single attacker dead, and it is evaded by a botnet.
 *
 * WHY THE ACCOUNT LIMIT DELAYS INSTEAD OF LOCKING
 * ------------------------------------------------
 * The operator's email address is published in this repository, so a
 * hard lockout on it is a denial of service waiting to be triggered:
 * anybody could fail ten times and lock the operator out for a
 * quarter of an hour, over and over, for as long as they liked.
 *
 * So the account limit is a DELAY. After a few failures every further
 * attempt waits a little longer, up to a few seconds, and the delay
 * applies whether the password was right or wrong. A guesser gets one
 * attempt every few seconds at worst; the operator, who knows the
 * password, is not held up by their own history.
 *
 * THE DELAY IS NOT AN ORACLE
 * --------------------------
 * The counter is keyed on the address that was TYPED, not on the
 * accounts that exist. An address nobody owns is counted exactly like
 * one that does, so "is there an account here?" has the same answer,
 * and takes the same time to give it, for every address.
 *
 * WHERE THE COUNTERS LIVE
 * -----------------------
 * In the database, in `admin_login_attempts`, so they survive a
 * restart and are shared by every instance of the app. There is an
 * in-memory fallback for a deployment where migration 0031 has not
 * been applied yet - the panel keeps working, and the limit is weaker
 * (it does not survive a restart, and it is not shared), which is
 * better than no panel at all.
 */

import { createServiceRoleClient } from "@/lib/supabase/server";
import { failureDelayMs, RATE_LIMIT } from "@/lib/admin-rate-limit-policy";

/*
 * The rules themselves - the delay, the thresholds, the address that
 * the request came from - live in `lib/admin-rate-limit-policy.ts`,
 * which has no imports and can be tested without a database.
 */
export {
  clientIpFromHeaders,
  failureDelayMs,
  RATE_LIMIT,
} from "@/lib/admin-rate-limit-policy";

/** Which counter a row belongs to. */
export type RateScope = "account" | "ip";

/** Where the counters are being kept for this request. */
export type RateMode = "database" | "memory";

/** What the limiter knows at one moment. */
export type LoginRateState = {
  mode: RateMode;
  /** Whether the request may not be tried at all. */
  blocked: boolean;
  /** How long to wait before answering, in milliseconds. */
  delayMs: number;
  /** Failures against this email address in the window. */
  accountFailures: number;
  /** Failures from this IP in the window. */
  ipFailures: number;
};

// ---------------------------------------------------------------------------
// The in-memory fallback
// ---------------------------------------------------------------------------

type MemoryEntry = { count: number; first: number };

/**
 * The counters, when the database cannot be reached.
 *
 * Bounded on purpose: an unbounded map keyed by whatever address a
 * caller sends is a memory leak with a remote trigger. When the map
 * is full the oldest entry goes, and the oldest is the one whose
 * window has most nearly expired anyway.
 */
const memoryAttempts = new Map<string, MemoryEntry>();

/** How many entries the fallback keeps. */
const MEMORY_LIMIT = 5000;

function memoryKey(scope: RateScope, subject: string): string {
  return `${scope}:${subject}`;
}

function memoryGet(scope: RateScope, subject: string): MemoryEntry | null {
  const key = memoryKey(scope, subject);
  const entry = memoryAttempts.get(key);
  if (!entry) return null;

  if (Date.now() - entry.first > RATE_LIMIT.windowMs) {
    memoryAttempts.delete(key);
    return null;
  }

  return entry;
}

function memoryRecord(scope: RateScope, subject: string): number {
  const now = Date.now();
  const key = memoryKey(scope, subject);
  const entry = memoryAttempts.get(key);

  let count: number;
  if (!entry || now - entry.first > RATE_LIMIT.windowMs) {
    count = 1;
    memoryAttempts.set(key, { count, first: now });
  } else {
    count = entry.count + 1;
    entry.count = count;
  }

  // Keep the map from growing without end.
  while (memoryAttempts.size > MEMORY_LIMIT) {
    const oldest = memoryAttempts.keys().next();
    if (oldest.done) break;
    memoryAttempts.delete(oldest.value);
  }

  return count;
}

function memoryClear(scope: RateScope, subject: string): void {
  memoryAttempts.delete(memoryKey(scope, subject));
}

// ---------------------------------------------------------------------------
// The database counters
// ---------------------------------------------------------------------------

type AttemptRow = {
  scope: string;
  subject: string;
  failures: number;
  first_attempt_at: string;
};

async function databaseRead(email: string, ip: string): Promise<AttemptRow[]> {
  const supabase = createServiceRoleClient();
  const { data, error } = await supabase
    .from("admin_login_attempts")
    .select("scope, subject, failures, first_attempt_at")
    .in("subject", [email, ip]);

  if (error) throw new Error(error.message);
  return (data as AttemptRow[] | null) ?? [];
}

async function databaseRecord(scope: RateScope, subject: string): Promise<number> {
  const supabase = createServiceRoleClient();
  const { data, error } = await supabase.rpc("admin_login_failure", {
    p_scope: scope,
    p_subject: subject,
  });

  if (error) throw new Error(error.message);
  return typeof data === "number" ? data : 0;
}

async function databaseClear(scope: RateScope, subject: string): Promise<void> {
  const supabase = createServiceRoleClient();
  const { error } = await supabase
    .from("admin_login_attempts")
    .delete()
    .eq("scope", scope)
    .eq("subject", subject);

  if (error) throw new Error(error.message);
}

// ---------------------------------------------------------------------------
// The limiter
// ---------------------------------------------------------------------------

/**
 * What the limiter knows about this email address and this IP.
 *
 * It also says WHERE the counters are, so that the caller records the
 * failure in the same place it read it. A request that read from the
 * database and then wrote to memory would split one counter in two
 * and make the limit weaker than it looks.
 */
export async function checkLoginRate(
  email: string,
  ip: string
): Promise<LoginRateState> {
  try {
    const rows = await databaseRead(email, ip);
    const now = Date.now();

    let accountFailures = 0;
    let ipFailures = 0;

    for (const row of rows) {
      const age = now - new Date(row.first_attempt_at).getTime();
      if (!Number.isFinite(age) || age > RATE_LIMIT.windowMs) continue;

      if (row.scope === "account") accountFailures = row.failures;
      if (row.scope === "ip") ipFailures = row.failures;
    }

    return {
      mode: "database",
      blocked: ipFailures >= RATE_LIMIT.ipMaxFailures,
      delayMs: failureDelayMs(accountFailures),
      accountFailures,
      ipFailures,
    };
  } catch {
    // Migration 0031 has not been applied, or the table cannot be
    // reached. The panel keeps working on the in-memory counters.
    const account = memoryGet("account", email);
    const fromIp = memoryGet("ip", ip);

    const accountFailures = account?.count ?? 0;
    const ipFailures = fromIp?.count ?? 0;

    return {
      mode: "memory",
      blocked: ipFailures >= RATE_LIMIT.ipMaxFailures,
      delayMs: failureDelayMs(accountFailures),
      accountFailures,
      ipFailures,
    };
  }
}

/**
 * Record a failed attempt, and say how many there have been.
 *
 * The account count is what the delay is worked out from, so it is
 * the one that is returned.
 */
export async function recordLoginFailure(
  email: string,
  ip: string,
  mode: RateMode
): Promise<number> {
  if (mode === "database") {
    try {
      await databaseRecord("ip", ip);
      return await databaseRecord("account", email);
    } catch {
      // Fall through to memory rather than losing the failure
      // entirely.
    }
  }

  memoryRecord("ip", ip);
  return memoryRecord("account", email);
}

/**
 * Forget the failures for this address and this IP.
 *
 * Called after a successful sign-in, because the person who just
 * signed in is not the person who was failing.
 */
export async function clearLoginFailures(
  email: string,
  ip: string,
  mode: RateMode
): Promise<void> {
  if (mode === "database") {
    try {
      await databaseClear("account", email);
      await databaseClear("ip", ip);
      return;
    } catch {
      // Fall through to memory.
    }
  }

  memoryClear("account", email);
  memoryClear("ip", ip);
}

/**
 * Wait for a while.
 *
 * Used for the account delay. A failed sign-in waits before it is
 * answered, so that guessing is slow.
 */
export function sleep(ms: number): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}
