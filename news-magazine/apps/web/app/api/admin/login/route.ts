import { NextRequest, NextResponse } from "next/server";

import {
  ADMIN_COOKIE,
  ADMIN_SESSION_SECONDS,
  HASH_FORMAT_VERSION,
  adminCookieOptions,
  hashNeedsUpgrade,
  hashPassword,
  sessionSecret,
  sessionSecretProblem,
  sessionSecretSource,
  signSession,
  verifyPassword,
} from "@/lib/admin-auth";
import {
  checkLoginRate,
  clearLoginFailures,
  clientIpFromHeaders,
  failureDelayMs,
  recordLoginFailure,
  sleep,
} from "@/lib/admin-rate-limit";
import { checkAdminSession } from "@/lib/admin-session";
import { crossSiteReason, isSameOriginRequest } from "@/lib/same-origin";
import { createServiceRoleClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Sign in to the admin panel.
 *
 * WHY THIS EXISTS BESIDE THE SUPABASE AUTH PATH
 * ---------------------------------------------
 * `middleware.ts` already gates /admin on a Supabase Auth session plus
 * a `user_roles` row. That path is untouched and still works. This is
 * a second door, for an operator who should not have to open a
 * Supabase dashboard to use their own site.
 *
 * WHAT IT WILL NOT DO
 * --------------------
 * It will not report which half of the credentials was wrong. An
 * "email not found" and a "wrong password" get the same answer, and
 * they take the same time, because an attacker who can tell the
 * difference can enumerate the accounts that exist.
 *
 * It will not answer a request that came from another site. See
 * `lib/same-origin.ts`.
 *
 * It limits how fast the password may be guessed, per email address
 * and per IP, in the database so that the limit survives a restart.
 * See `lib/admin-rate-limit.ts`.
 */

type LoginBody = {
  email?: unknown;
  password?: unknown;
  redirect?: unknown;
};

/**
 * The longest an email address may be.
 *
 * RFC 5321 says 254 characters; 320 is the practical limit. A longer
 * one is not an email address, it is a payload, and there is no reason
 * to hash it.
 */
const MAX_EMAIL_LENGTH = 320;

/**
 * The longest a password may be.
 *
 * A password longer than this is a mistake or an attack, and hashing a
 * megabyte of text on every attempt is a way of making the machine
 * busy for nothing.
 */
const MAX_PASSWORD_LENGTH = 512;

type AdminRow = {
  id: string;
  email: string;
  password_hash: string;
  is_active: boolean;
  /** Which hashing policy the stored hash was written under. */
  password_hash_version?: number | null;
};

/**
 * The path to send the reader to after signing in.
 *
 * Only a path ON THIS SITE. An absolute URL, a protocol-relative one,
 * or anything that does not start with a single slash would turn the
 * login form into an open redirect - a link that looks like ours and
 * lands the reader somewhere else, carrying whatever they typed into
 * the password box.
 */
function safeRedirect(target: unknown): string {
  if (typeof target !== "string") return "/admin";
  if (!target.startsWith("/")) return "/admin";
  // "//evil.example" is protocol-relative, not a local path.
  if (target.startsWith("//")) return "/admin";
  return target;
}

/**
 * Read the admin row for an email address.
 *
 * The hashing version column arrived with migration 0031, and a
 * deployment that has not applied it yet must still be able to sign
 * in. So the column is asked for first and the query is repeated
 * without it if the database says it does not exist.
 */
async function loadAdminUser(email: string): Promise<
  | { status: "ok"; row: AdminRow | null }
  | { status: "not-migrated" }
  | { status: "error"; message: string }
> {
  const supabase = createServiceRoleClient();

  const withVersion = await supabase
    .from("admin_users")
    .select("id, email, password_hash, is_active, password_hash_version")
    .eq("email", email)
    .maybeSingle();

  if (!withVersion.error) {
    return { status: "ok", row: (withVersion.data as AdminRow | null) ?? null };
  }

  const missingColumn =
    withVersion.error.code === "42P01" ||
    withVersion.error.code === "PGRST205" ||
    /does not exist|not found/i.test(withVersion.error.message ?? "");

  if (!missingColumn) {
    return { status: "error", message: withVersion.error.message };
  }

  // The column is not there. Ask again without it.
  const plain = await supabase
    .from("admin_users")
    .select("id, email, password_hash, is_active")
    .eq("email", email)
    .maybeSingle();

  if (plain.error) {
    return { status: "not-migrated" };
  }

  return { status: "ok", row: (plain.data as AdminRow | null) ?? null };
}

/**
 * Move an account onto a hash of its own.
 *
 * The seeded hash has a salt that is published in the repository,
 * because it had to be written down for the operator to use it. This
 * rewrites the hash with a salt generated for this one account, the
 * first time the account signs in, and leaves the password alone. The
 * operator does not have to do anything, and no two accounts share a
 * salt.
 *
 * It does NOT change the password, so it does not make a published
 * password safe. Only choosing a new password does that.
 *
 * Best effort: if it fails, the sign-in still succeeds. A hash that
 * is slightly old is not a broken sign-in.
 */
async function upgradeStoredHash(
  adminId: string,
  password: string,
  storedHash: string | null | undefined,
  version: number | null | undefined
): Promise<void> {
  if (!hashNeedsUpgrade(storedHash, version)) return;

  try {
    const fresh = await hashPassword(password);
    const supabase = createServiceRoleClient();

    const { error } = await supabase
      .from("admin_users")
      .update({
        password_hash: fresh,
        password_hash_version: HASH_FORMAT_VERSION,
        password_updated_at: new Date().toISOString(),
      })
      .eq("id", adminId);

    if (error) {
      // The columns are from migration 0031. A deployment that has not
      // applied it keeps the old hash, which still works.
      console.warn("[admin] could not upgrade the stored hash:", error.message);
      return;
    }

    console.log(`[admin] upgraded the stored hash for ${adminId}`);
  } catch (error) {
    console.warn("[admin] could not upgrade the stored hash:", error);
  }
}

export async function POST(request: NextRequest) {
  /*
    WHERE THE REQUEST CAME FROM

    A state-changing request that another site made is not this site's
    request. The check is here, before anything else, so that a
    cross-site attempt cannot spend the operator's rate limit budget
    either.

    A browser always sends `Sec-Fetch-Site` on a cross-site request,
    and sends `Origin` on every POST, so a cross-site form cannot get
    past this. A command line client sends neither and is not a
    browser, so it is allowed through - which is what lets the cron
    and the operator's own scripts talk to the API.
  */
  if (!isSameOriginRequest(request.headers)) {
    console.warn(
      `[admin] refused a sign-in from another site (${crossSiteReason(
        request.headers
      )})`
    );
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "CROSS_SITE_REQUEST",
          message: "This sign-in request did not come from this site.",
        },
      },
      { status: 403 },
    );
  }

  let body: LoginBody;
  try {
    body = (await request.json()) as LoginBody;
  } catch {
    return NextResponse.json(
      {
        success: false,
        error: { code: "BAD_REQUEST", message: "Send email and password as JSON." },
      },
      { status: 400 },
    );
  }

  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const password = typeof body.password === "string" ? body.password : "";

  if (!email || !password) {
    return NextResponse.json(
      {
        success: false,
        error: { code: "BAD_REQUEST", message: "Email and password are required." },
      },
      { status: 400 },
    );
  }

  if (email.length > MAX_EMAIL_LENGTH || password.length > MAX_PASSWORD_LENGTH) {
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "BAD_REQUEST",
          message: "That email address or password is too long.",
        },
      },
      { status: 400 },
    );
  }

  const secret = sessionSecret();
  if (!secret) {
    // The reason is spelled out: "not configured" on its own tells the
    // operator nothing about which variable to fix.
    const problem = sessionSecretProblem();
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "NOT_CONFIGURED",
          message:
            problem ??
            "No session secret is configured. Set ADMIN_SESSION_SECRET, or SUPABASE_SERVICE_ROLE_KEY, on the deployment.",
        },
      },
      { status: 503 },
    );
  }

  const ip = clientIpFromHeaders(request.headers);

  /*
    HOW FAST MAY THE PASSWORD BE GUESSED

    The limit is in the database, so it survives a restart, and it
    counts the email address as well as the IP. The address limit is
    a delay rather than a lock, because the operator's address is
    published in the repository and a lock would be a denial of
    service with a search engine's index to find it.
  */
  const rate = await checkLoginRate(email, ip);

  if (rate.blocked) {
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "RATE_LIMITED",
          message: "Too many sign-in attempts. Try again in a few minutes.",
        },
      },
      { status: 429 },
    );
  }

  const loaded = await loadAdminUser(email).catch((error) => {
    console.error("[admin] login could not read the admin table:", error);
    return { status: "error", message: String(error) } as const;
  });

  if (loaded.status === "not-migrated") {
    // The table missing means migration 0017 has not been applied.
    // Say so plainly: the operator needs to know which step to do.
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "NOT_MIGRATED",
          message:
            "The admin tables are not there yet. Apply supabase/migrations/0017_admin_users.sql and try again.",
        },
      },
      { status: 503 },
    );
  }

  if (loaded.status === "error") {
    console.error("[admin] login could not read the admin table:", loaded.message);
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "SERVER_ERROR",
          message: "The sign-in service is not reachable. Try again shortly.",
        },
      },
      { status: 503 },
    );
  }

  const adminUser = loaded.row;

  /*
    The password is verified whether or not the account exists.

    A missing account and a wrong password must take the same time and
    produce the same answer, or the form becomes a way of discovering
    which email addresses are administrators. When the account does
    not exist, `verifyPassword` derives a key from the supplied
    password against a hash of a password nobody kept, and the result
    is discarded.
  */
  const ok = await verifyPassword(password, adminUser?.password_hash);

  if (!adminUser || !ok || !adminUser.is_active) {
    const failures = await recordLoginFailure(email, ip, rate.mode);
    const delay = failureDelayMs(failures);

    if (delay > 0) {
      // The wait happens BEFORE the answer is sent, so that guessing
      // is slow. It is the same wait for every address, whether or
      // not the account exists.
      await sleep(delay);
    }

    return NextResponse.json(
      {
        success: false,
        error: {
          code: "INVALID_CREDENTIALS",
          message: "That email and password do not match.",
        },
      },
      { status: 401 },
    );
  }

  // Signed in. The counters for this address are cleared: the person
  // who just signed in is not the person who was failing.
  await clearLoginFailures(email, ip, rate.mode);

  // Give the account a hash of its own if it is still on the
  // published one.
  await upgradeStoredHash(
    adminUser.id,
    password,
    adminUser.password_hash,
    adminUser.password_hash_version ?? null
  );

  // Write the session row first, so the cookie can only ever name a
  // session that exists.
  const expiresAt = Math.floor(Date.now() / 1000) + ADMIN_SESSION_SECONDS;
  const sessionId = crypto.randomUUID();

  try {
    const supabase = createServiceRoleClient();

    // Remove this admin's sessions that have already expired. They
    // are not used again, and a table of dead sessions is a table
    // nobody reads.
    await supabase
      .from("admin_sessions")
      .delete()
      .eq("admin_user_id", adminUser.id)
      .lt("expires_at", new Date().toISOString());

    const { error } = await supabase.from("admin_sessions").insert({
      id: sessionId,
      admin_user_id: adminUser.id,
      expires_at: new Date(expiresAt * 1000).toISOString(),
      user_agent: request.headers.get("user-agent") ?? null,
    });

    if (error) throw error;

    // Record the sign-in, so the operator can see when the account
    // was last used.
    await supabase
      .from("admin_users")
      .update({ last_login_at: new Date().toISOString() })
      .eq("id", adminUser.id);
  } catch (error) {
    console.error("[admin] could not record the session:", error);
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "SERVER_ERROR",
          message: "The session could not be started. Try again shortly.",
        },
      },
      { status: 503 },
    );
  }

  const session = {
    uid: adminUser.id,
    email: adminUser.email,
    sid: sessionId,
    exp: expiresAt,
  };

  const cookie = await signSession(session, secret);

  const response = NextResponse.json({
    success: true,
    redirect: safeRedirect(body.redirect),
  });

  response.cookies.set(ADMIN_COOKIE, cookie, adminCookieOptions(ADMIN_SESSION_SECONDS));

  // Recorded for the operator, never shown to the reader.
  console.log(
    `[admin] signed in as ${adminUser.email} (secret source: ${sessionSecretSource()})`,
  );

  return response;
}

/**
 * Who is signed in, if anyone.
 *
 * Used by the dashboard to decide what to render, and by the client to
 * show the signed-in state without a full page load.
 *
 * The session ROW is checked, not just the cookie: a cookie whose
 * session has been revoked says no here, which is the whole point of
 * being able to revoke one.
 */
export async function GET(request: NextRequest) {
  const check = await checkAdminSession(request.cookies.get(ADMIN_COOKIE)?.value);

  if (!check.ok) {
    return NextResponse.json({ signedIn: false }, { status: 200 });
  }

  return NextResponse.json({
    signedIn: true,
    email: check.session.email,
    expiresAt: check.session.exp,
  });
}
