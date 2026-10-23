/**
 * Admin sign-in: password verification and session cookies.
 *
 * TWO RUNTIMES, ONE IMPLEMENTATION
 * --------------------------------
 * The login route runs in the Node runtime and the middleware runs in
 * the Edge runtime. Node's `crypto` module is not available in Edge,
 * and Edge's Web Crypto is not available in every Node build, so
 * everything here is written against the Web Crypto API alone - which
 * both runtimes have. That is why the password hash is PBKDF2 rather
 * than bcrypt: Web Crypto has PBKDF2 built in and bcrypt would have
 * meant a native dependency in a bundle that has to compile for two
 * different runtimes.
 *
 * WHAT THIS IS NOT
 * ----------------
 * This is NOT a replacement for the Supabase Auth path that
 * `middleware.ts` already had. That path still works, and a user with
 * an `admin` or `super_admin` row in `user_roles` gets in exactly as
 * before. This is a second door, for an operator who should not have
 * to open a dashboard to use one.
 *
 * THE SECRET
 * ----------
 * The session cookie is signed with an HMAC, and the signing key has
 * to be the SAME on every request - a Vercel function can be a
 * different instance on the next request, and a key generated per
 * process would invalidate every session on every cold start. So the
 * key comes from the environment, with a documented order of
 * preference:
 *
 *   1. ADMIN_SESSION_SECRET   - the right answer
 *   2. SUPABASE_SERVICE_ROLE_KEY - already in the deployment, and it
 *      is a secret that is not published to the browser
 *   3. INGESTION_SECRET       - also already in the deployment
 *
 * The fallback exists so the panel works the moment it is deployed
 * without the operator having to set anything, and the alternative
 * was a panel that 404s until a secret is added.
 *
 * ROTATION
 * --------
 * A signing key that never changes is a key that is eventually
 * compromised. `ADMIN_SESSION_SECRET_PREVIOUS` is accepted when a
 * cookie is READ and never when one is written, so the key can be
 * replaced without signing everybody out at once: sign with the new
 * key, leave the old one in place for the length of the longest
 * session, and take it away afterwards.
 */

// ---------------------------------------------------------------------------
// Password hashing
// ---------------------------------------------------------------------------

/** The iteration count a stored hash was made with. */
export const PBKDF2_ITERATIONS = 210_000;

/**
 * The iteration count NEW hashes are made with.
 *
 * The count travels WITH the hash, so this is only the count a fresh
 * hash gets. A hash made with an older count keeps verifying against
 * its own, and `hashNeedsUpgrade` is what notices and rewrites it.
 */
const TARGET_ITERATIONS = PBKDF2_ITERATIONS;

/** How many bytes of salt a hash carries. */
const SALT_BYTES = 16;

/** The length of the derived key, in bytes. SHA-256 output. */
const KEY_BYTES = 32;

/**
 * Which hashing policy a stored hash was written under.
 *
 * Version 1 is what migration 0017 seeded: a correct PBKDF2 hash,
 * but with a salt that is published in the repository.
 *
 * Version 2 is what `hashPassword` writes: the same PBKDF2 shape with
 * a salt generated for that one account and never published. The
 * version lives in a column beside the hash, so the login route can
 * tell "this hash needs rewriting" from "this hash is fine", and can
 * rewrite it the first time the account signs in - which moves the
 * account onto a salt of its own without the operator having to type
 * a password again.
 */
export const HASH_FORMAT_VERSION = 2;

/**
 * A hash of a password nobody kept.
 *
 * It exists for one reason: TIMING. When the email does not belong to
 * an account, the login route still derives a key from the supplied
 * password against this record, so a request for an unknown address
 * costs the same as a request for a known one. Without it, "no such
 * account" answers faster than "wrong password", and the form becomes
 * a way of listing which addresses are administrators.
 *
 * It used to be the seeded operator's own hash, which was wrong in two
 * ways at once: it put a live credential in a second place, and it
 * meant the "unknown account" path was doing exactly the same work as
 * the "known account" path for a reason that had nothing to do with
 * timing. The password behind this record was generated at random and
 * thrown away, so it cannot be used to sign in to anything.
 */
const DUMMY_HASH =
  "pbkdf2-sha256$210000$npYtxNJJM8J0AdgYpiSvJg==$0NghZXxpFCn/YNg1RsewduLUYckt9j9FjfXnFPHVVE4=";

/**
 * The dummy, exported so that a test can pin its shape.
 *
 * It is not a secret: the password behind it was generated at random
 * and thrown away, so there is nothing to guess and nothing to steal.
 * What the test pins is the ITERATION COUNT, which is the thing that
 * matters - a dummy that cost a different amount of work from a real
 * comparison would make "no such account" answer at a different speed
 * from "wrong password", and the whole point of it would be lost.
 */
export const DUMMY_PASSWORD_HASH = DUMMY_HASH;

/**
 * The dummy, parsed once.
 *
 * It is parsed at load time so that a malformed dummy - which would be
 * a mistake in this file rather than in anybody's data - cannot turn
 * into a thrown error on the path that exists to NEVER throw.
 */
const DUMMY_RECORD = parseHashRecord(DUMMY_HASH);

/** The shape of a stored hash, once it has been parsed. */
type HashRecord = {
  iterations: number;
  salt: Uint8Array;
  expected: Uint8Array;
};

/**
 * Parse a stored hash, or null when it is not one of ours.
 *
 * The checks are strict on purpose. A record with a different scheme,
 * a salt shorter than the salt we write, or a key that is not 32
 * bytes was not written by `hashPassword`, and treating it as one
 * would mean comparing a derived key of the wrong length against a
 * stored one - which `timingSafeEqual` refuses on length alone, and
 * which would answer faster than a real comparison.
 */
function parseHashRecord(stored: string | null | undefined): HashRecord | null {
  if (!stored) return null;

  const parts = stored.split("$");
  if (parts.length !== 4) return null;

  const scheme = parts[0];
  const iterationsRaw = parts[1];
  const saltRaw = parts[2];
  const hashRaw = parts[3];
  if (!scheme || !iterationsRaw || !saltRaw || !hashRaw) return null;
  if (scheme !== "pbkdf2-sha256") return null;

  const iterations = Number.parseInt(iterationsRaw, 10);
  if (!Number.isFinite(iterations) || iterations < 1) return null;

  try {
    const salt = base64ToBytes(saltRaw);
    const expected = base64ToBytes(hashRaw);
    if (salt.length < SALT_BYTES) return null;
    if (expected.length !== KEY_BYTES) return null;

    return { iterations, salt, expected };
  } catch {
    return null;
  }
}

/**
 * Derive a key from a password and a record.
 *
 * The length of the derived key follows the record, so a comparison is
 * always between two buffers of the same size.
 */
async function deriveKey(
  password: string,
  record: HashRecord
): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );

  const derived = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: record.salt, iterations: record.iterations, hash: "SHA-256" },
    key,
    record.expected.length * 8
  );

  return new Uint8Array(derived);
}

/**
 * Verify a password against a stored hash.
 *
 * The stored form is `pbkdf2-sha256$<iterations>$<salt base64>$<hash
 * base64>`. The iteration count travels WITH the hash rather than being
 * read from a constant, so raising it later does not invalidate every
 * existing password: a new hash is written with the new count and old
 * ones keep verifying against their own.
 *
 * The comparison is constant-time. `crypto.subtle.verify` does it for
 * us, and it does not stop early on the first differing byte, so a
 * caller cannot learn the hash by timing the rejections.
 */
export async function verifyPassword(
  password: string,
  stored: string | null | undefined
): Promise<boolean> {
  const record = parseHashRecord(stored);

  if (!record) {
    // A missing or malformed record still costs the same as a real
    // comparison, so that "this account has no usable hash" cannot be
    // told apart from "wrong password" by how quickly the answer
    // comes back. The result is discarded: the answer is no.
    if (DUMMY_RECORD) await deriveKey(password, DUMMY_RECORD);
    return false;
  }

  try {
    const derived = await deriveKey(password, record);
    return timingSafeEqual(derived, record.expected);
  } catch {
    return false;
  }
}

/**
 * Make a new hash for a password.
 *
 * The salt is sixteen random bytes, generated for this one hash and
 * never published. Two accounts with the same password therefore have
 * different hashes, and a dictionary built from one hash says nothing
 * about the other.
 */
export async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const record: HashRecord = {
    iterations: TARGET_ITERATIONS,
    salt,
    expected: new Uint8Array(KEY_BYTES),
  };

  const derived = await deriveKey(password, record);

  return `pbkdf2-sha256$${TARGET_ITERATIONS}$${bytesToBase64(
    salt
  )}$${bytesToBase64(derived)}`;
}

/**
 * Whether a stored hash should be replaced with a fresh one.
 *
 * `version` is the row's `password_hash_version` column. A row written
 * before version 2 has a published salt, and rewriting it at the next
 * sign-in moves it onto a salt of its own.
 *
 * The check also covers the iteration count, so raising
 * `TARGET_ITERATIONS` later upgrades every account as each one signs
 * in rather than invalidating all of them at once.
 */
export function hashNeedsUpgrade(
  stored: string | null | undefined,
  version: number | null | undefined
): boolean {
  if (version === null || version === undefined) return false;
  if (version < HASH_FORMAT_VERSION) return true;

  const record = parseHashRecord(stored);
  if (!record) return false;

  return record.iterations !== TARGET_ITERATIONS;
}

/**
 * Constant-time byte comparison.
 *
 * `crypto.subtle.verify` only exists for keyed operations, so a plain
 * HMAC of the hash is compared by hand here. Every byte is examined
 * and the differences are accumulated, so the time taken does not
 * depend on WHERE the first difference is.
 */
function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;

  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  }
  return diff === 0;
}

/**
 * Compare two strings without leaking where they differ.
 *
 * Used for anything that is a SECRET rather than a password: the
 * cron secret on the admin sync route, for instance, where a `===`
 * answers a little faster for every matching prefix and a caller can
 * walk the secret out one character at a time by timing the answers.
 */
export function constantTimeStringEqual(a: string, b: string): boolean {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);

  // The lengths are compared first, which leaks the length and not the
  // content. Every real secret compared here is a fixed length, and a
  // length that leaks is far less useful than a prefix that leaks.
  if (left.length !== right.length) return false;

  return timingSafeEqual(left, right);
}

// ---------------------------------------------------------------------------
// Session cookie
// ---------------------------------------------------------------------------

/** The cookie the signed session travels in. */
export const ADMIN_COOKIE = "news_admin_session";

/** How long a signed-in session lasts. */
export const ADMIN_SESSION_SECONDS = 60 * 60 * 8;

/**
 * The shortest a DEDICATED signing secret may be.
 *
 * The dedicated secret is the operator's own, and it is generated by
 * them, so there is no reason for it to be short. Thirty-two characters
 * of anything is more than the HMAC can usefully use.
 */
export const MIN_SECRET_LENGTH = 32;

/**
 * The shortest a FALLBACK signing secret may be.
 *
 * The fallbacks are secrets that already exist for another job, so
 * they are shorter than a dedicated one would be. Sixteen characters
 * is still far more than enough for an HMAC key, and it is below the
 * length of every secret this project actually ships.
 */
export const MIN_FALLBACK_SECRET_LENGTH = 16;

/** The shape carried in the cookie, signed. */
export type AdminSession = {
  /** The row in `admin_users`. */
  uid: string;
  email: string;
  /** The row in `admin_sessions`; deleting it ends the session at once. */
  sid: string;
  /** Unix seconds. */
  exp: number;
};

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The bytes of a base64url string. */
function base64UrlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function base64ToBytes(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/**
 * The HMAC key, or null when no secret is configured.
 *
 * Null is a real possibility and it is handled: `signSession` then
 * refuses to sign, and the login route answers 503 with an
 * explanation. A panel that silently issues unsigned cookies would be
 * worse than one that says it is not configured.
 *
 * A secret that is too SHORT is treated as no secret at all. An HMAC
 * key is only as strong as the entropy behind it, and a short one is
 * a key somebody can guess. Refusing to sign with it is the safe
 * answer; the operator is told exactly which variable to fix.
 */
export function sessionSecret(): string | null {
  const dedicated = process.env.ADMIN_SESSION_SECRET;
  if (dedicated && dedicated.trim().length > 0) {
    return dedicated.trim().length >= MIN_SECRET_LENGTH ? dedicated.trim() : null;
  }

  const fallback =
    process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.INGESTION_SECRET || null;
  if (fallback && fallback.trim().length >= MIN_FALLBACK_SECRET_LENGTH) {
    return fallback.trim();
  }

  return null;
}

/**
 * The key that was in use before the current one.
 *
 * Cookies signed with it are still accepted, so the key can be
 * replaced without signing every signed-in operator out at once. It is
 * never used to SIGN anything.
 */
export function previousSessionSecret(): string | null {
  const previous = process.env.ADMIN_SESSION_SECRET_PREVIOUS;
  if (!previous) return null;
  const trimmed = previous.trim();
  if (trimmed.length < MIN_SECRET_LENGTH) return null;
  return trimmed;
}

/**
 * Every key a cookie may be verified against, best first.
 */
export function sessionSecrets(): string[] {
  const secrets: string[] = [];

  const current = sessionSecret();
  if (current) secrets.push(current);

  const previous = previousSessionSecret();
  if (previous && !secrets.includes(previous)) secrets.push(previous);

  return secrets;
}

/**
 * Why there is no usable signing secret, in words.
 *
 * Null when there is one. The message is for the operator and is
 * shown on the sign-in page, because "the panel is not configured" is
 * useless on its own - it has to say WHICH variable to set or
 * lengthen.
 */
export function sessionSecretProblem(): string | null {
  const dedicated = process.env.ADMIN_SESSION_SECRET;
  if (dedicated && dedicated.trim().length > 0) {
    if (dedicated.trim().length < MIN_SECRET_LENGTH) {
      return (
        `ADMIN_SESSION_SECRET is set but is only ${dedicated.trim().length} ` +
        `characters. It signs the session cookie, so it has to be at ` +
        `least ${MIN_SECRET_LENGTH}. Set it to a long random string, ` +
        `for example the output of "openssl rand -hex 32".`
      );
    }
    return null;
  }

  const fallback =
    process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.INGESTION_SECRET || null;
  if (!fallback || fallback.trim().length === 0) {
    return (
      "No session secret is configured. Set ADMIN_SESSION_SECRET (at " +
      `least ${MIN_SECRET_LENGTH} characters), or SUPABASE_SERVICE_ROLE_KEY, ` +
      "on the deployment."
    );
  }

  if (fallback.trim().length < MIN_FALLBACK_SECRET_LENGTH) {
    return (
      `The signing secret that is configured is only ${fallback.trim().length} ` +
      `characters long, which is not enough to sign a session cookie with. ` +
      `Set ADMIN_SESSION_SECRET to a random string of at least ` +
      `${MIN_SECRET_LENGTH} characters.`
    );
  }

  return null;
}

/** Which of the secrets is actually in use. Logged, never shown. */
export function sessionSecretSource(): string {
  if (process.env.ADMIN_SESSION_SECRET) return "ADMIN_SESSION_SECRET";
  if (process.env.SUPABASE_SERVICE_ROLE_KEY) return "SUPABASE_SERVICE_ROLE_KEY";
  if (process.env.INGESTION_SECRET) return "INGESTION_SECRET";
  return "none";
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

/**
 * The delimiter between the fields inside the signed payload.
 *
 * It is the ASCII unit separator, and it is here for a reason that
 * cost a debugging session: the payload contains the operator's EMAIL
 * ADDRESS, and an email address contains a dot. Splitting the payload
 * on "." therefore produced five fields for every real session
 * ("uid" "user" "name@example" "com" "sid" "exp") rather than the four
 * the format expects, so every correctly signed cookie was rejected
 * and no one could sign in.
 *
 * A control character cannot appear in an email address, in a UUID,
 * or in a Unix timestamp, so the split is now unambiguous whatever
 * the address looks like.
 */
const FIELD_SEPARATOR = "\u001f";

/**
 * Sign a session payload.
 *
 * The payload is `uid<sep>email<sep>sid<sep>exp` and the signature
 * covers it, so a tampered expiry or a swapped user id is detected.
 * The cookie is `payload.signature`, both base64url.
 */
export async function signSession(
  session: AdminSession,
  secret: string
): Promise<string> {
  const payload = [
    session.uid,
    session.email,
    session.sid,
    String(session.exp),
  ].join(FIELD_SEPARATOR);
  const encoded = base64UrlEncode(new TextEncoder().encode(payload));
  const key = await hmacKey(secret);
  const signature = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(encoded))
  );

  return `${encoded}.${base64UrlEncode(signature)}`;
}

/**
 * Verify a cookie and return the session it carries.
 *
 * Null for anything that is not a valid, unexpired, correctly signed
 * cookie. A caller must treat null as "not signed in" and never as
 * "signed in but something is wrong with the details".
 */
export async function readSession(
  cookie: string | null | undefined,
  secret: string | null
): Promise<AdminSession | null> {
  if (!secret) return null;
  return readSessionWithSecrets(cookie, [secret]);
}

/**
 * Verify a cookie against any of the keys in use.
 *
 * The current key is tried first and the previous one second, so a
 * key that is being rotated costs one extra HMAC on the cookies signed
 * with the old key and nothing at all on the new ones.
 */
export async function readSessionWithSecrets(
  cookie: string | null | undefined,
  secrets: string[]
): Promise<AdminSession | null> {
  if (!cookie || secrets.length === 0) return null;

  const parts = cookie.split(".");
  if (parts.length !== 2) return null;

  const encoded = parts[0];
  const signatureRaw = parts[1];
  if (!encoded || !signatureRaw) return null;

  let payloadBytes: Uint8Array;
  try {
    payloadBytes = base64UrlToBytes(encoded);
  } catch {
    return null;
  }

  let valid = false;
  for (const secret of secrets) {
    try {
      const key = await hmacKey(secret);
      const ok = await crypto.subtle.verify(
        "HMAC",
        key,
        base64UrlToBytes(signatureRaw),
        new TextEncoder().encode(encoded)
      );
      if (ok) {
        valid = true;
        break;
      }
    } catch {
      // A secret that cannot even be turned into a key is not a
      // reason to refuse the whole cookie; try the next one.
    }
  }

  if (!valid) return null;

  try {
    const payload = new TextDecoder().decode(payloadBytes);
    const fields = payload.split(FIELD_SEPARATOR);
    if (fields.length !== 4) return null;

    const uid = fields[0];
    const email = fields[1];
    const sid = fields[2];
    const expRaw = fields[3];
    if (!uid || !email || !sid || !expRaw) return null;

    const exp = Number.parseInt(expRaw, 10);
    if (!Number.isFinite(exp)) return null;

    // An expired cookie is not a session, whatever its signature says.
    if (exp * 1000 <= Date.now()) return null;

    return { uid, email, sid, exp };
  } catch {
    return null;
  }
}

/**
 * The cookie's attributes.
 *
 * `httpOnly` so a script cannot read it, `sameSite=lax` so it is not
 * sent with a cross-site POST, and `secure` in production so it is
 * never sent over plain HTTP. `path: "/"` because the admin panel is
 * not the only thing that has to carry it.
 *
 * `sameSite=lax` rather than `strict` on purpose: a link to the panel
 * from an email or a bookmark has to carry the cookie, and a strict
 * cookie would not be sent on that navigation and the operator would
 * be told to sign in again while still signed in. The cross-site POST
 * that lax still allows is closed by the origin check on the admin
 * routes, not by the cookie.
 */
export function adminCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
