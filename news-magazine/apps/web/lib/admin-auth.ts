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
 */

// ---------------------------------------------------------------------------
// Password hashing
// ---------------------------------------------------------------------------

/** The iteration count a stored hash was made with. */
const PBKDF2_ITERATIONS = 210_000;

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
  if (!stored) return false;

  const parts = stored.split("$");
  if (parts.length !== 4) return false;

  const scheme = parts[0];
  const iterationsRaw = parts[1];
  const saltRaw = parts[2];
  const hashRaw = parts[3];
  if (!scheme || !iterationsRaw || !saltRaw || !hashRaw) return false;
  if (scheme !== "pbkdf2-sha256") return false;

  const iterations = Number.parseInt(iterationsRaw, 10);
  if (!Number.isFinite(iterations) || iterations < 1) return false;

  try {
    const salt = base64ToBytes(saltRaw);
    const expected = base64ToBytes(hashRaw);

    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(password),
      "PBKDF2",
      false,
      ["deriveBits"]
    );

    const derived = await crypto.subtle.deriveBits(
      { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
      key,
      expected.byteLength * 8
    );

    return timingSafeEqual(new Uint8Array(derived), expected);
  } catch {
    return false;
  }
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

// ---------------------------------------------------------------------------
// Session cookie
// ---------------------------------------------------------------------------

/** The cookie the signed session travels in. */
export const ADMIN_COOKIE = "news_admin_session";

/** How long a signed-in session lasts. */
export const ADMIN_SESSION_SECONDS = 60 * 60 * 8;

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

/**
 * The HMAC key, or null when no secret is configured.
 *
 * Null is a real possibility and it is handled: `signSession` then
 * refuses to sign, and the login route answers 503 with an
 * explanation. A panel that silently issues unsigned cookies would be
 * worse than one that says it is not configured.
 */
export function sessionSecret(): string | null {
  const candidates = [
    process.env.ADMIN_SESSION_SECRET,
    process.env.SUPABASE_SERVICE_ROLE_KEY,
    process.env.INGESTION_SECRET,
  ];

  for (const candidate of candidates) {
    if (candidate && candidate.trim().length > 0) return candidate;
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
  if (!cookie || !secret) return null;

  const parts = cookie.split(".");
  if (parts.length !== 2) return null;

  const encoded = parts[0];
  const signatureRaw = parts[1];
  if (!encoded || !signatureRaw) return null;

  try {
    const key = await hmacKey(secret);
    const valid = await crypto.subtle.verify(
      "HMAC",
      key,
      base64UrlToBytes(signatureRaw),
      new TextEncoder().encode(encoded)
    );
    if (!valid) return null;

    const payload = new TextDecoder().decode(
      base64UrlToBytes(encoded)
    );
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
