import { NextRequest, NextResponse } from "next/server";

import {
  ADMIN_COOKIE,
  ADMIN_SESSION_SECONDS,
  adminCookieOptions,
  readSession,
  sessionSecret,
  sessionSecretSource,
  signSession,
  verifyPassword,
} from "@/lib/admin-auth";
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
 * It also rate-limits per IP, in memory, because a login form with no
 * limit is a password-guessing oracle.
 */

type LoginBody = {
  email?: unknown;
  password?: unknown;
  redirect?: unknown;
};

/** How many failed attempts one address may make in the window. */
const MAX_ATTEMPTS = 8;
/** The window, in milliseconds. */
const ATTEMPT_WINDOW_MS = 10 * 60 * 1000;

/**
 * Failed attempts, in memory.
 *
 * In memory is a limitation and it is the right one for now: this
 * process is a single instance on Vercel, the limit exists to make
 * guessing slow rather than to be a security boundary, and a table of
 * failed attempts would be a table an attacker can fill. A distributed
 * limit belongs in the database if this ever runs at scale.
 */
const attempts = new Map<string, { count: number; first: number }>();

function rateLimited(key: string): boolean {
  const now = Date.now();
  const entry = attempts.get(key);

  if (!entry) return false;

  // The window has passed, so the slate is clean again.
  if (now - entry.first > ATTEMPT_WINDOW_MS) {
    attempts.delete(key);
    return false;
  }

  return entry.count >= MAX_ATTEMPTS;
}

function recordFailure(key: string): void {
  const now = Date.now();
  const entry = attempts.get(key);

  if (!entry || now - entry.first > ATTEMPT_WINDOW_MS) {
    attempts.set(key, { count: 1, first: now });
    return;
  }

  entry.count += 1;
}

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

export async function POST(request: NextRequest) {
  const client = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const ip = client || "unknown";

  if (rateLimited(ip)) {
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

  const secret = sessionSecret();
  if (!secret) {
    return NextResponse.json(
      {
        success: false,
        error: {
          code: "NOT_CONFIGURED",
          message:
            "No session secret is configured. Set ADMIN_SESSION_SECRET, or SUPABASE_SERVICE_ROLE_KEY, on the deployment.",
        },
      },
      { status: 503 },
    );
  }

  type AdminRow = {
    id: string;
    email: string;
    password_hash: string;
    is_active: boolean;
  };

  let adminUser: AdminRow | null = null;

  try {
    const supabase = createServiceRoleClient();
    const { data, error } = await supabase
      .from("admin_users")
      .select("id, email, password_hash, is_active")
      .eq("email", email)
      .maybeSingle();

    if (error) {
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

    adminUser = (data as AdminRow | null) ?? null;
  } catch (error) {
    console.error("[admin] login could not read the admin table:", error);
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

  /*
    The password is verified whether or not the account exists.

    A missing account and a wrong password must take the same time and
    produce the same answer, or the form becomes a way of discovering
    which email addresses are administrators. When the account does
    not exist, a hash is still derived from the supplied password
    against a fixed salt, and the result is discarded.
  */
  const storedHash =
    adminUser?.password_hash ??
    "pbkdf2-sha256$210000$9eAfn0QvNAWZhY8Q+dtMiQ==$a2ZYetjYH2HHf35j749aVfGQpGOqEkjfUGBn/vseolw=";

  const ok = await verifyPassword(password, storedHash);

  if (!adminUser || !ok || !adminUser.is_active) {
    recordFailure(ip);
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

  // Signed in. Write the session row first, so the cookie can only
  // ever name a session that exists.
  const expiresAt = Math.floor(Date.now() / 1000) + ADMIN_SESSION_SECONDS;
  const sessionId = crypto.randomUUID();

  try {
    const supabase = createServiceRoleClient();
    const { error } = await supabase.from("admin_sessions").insert({
      id: sessionId,
      admin_user_id: adminUser.id,
      expires_at: new Date(expiresAt * 1000).toISOString(),
      user_agent: request.headers.get("user-agent") ?? null,
    });

    if (error) throw error;
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

  // A successful sign-in clears the slate for this address.
  attempts.delete(ip);

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
 */
export async function GET(request: NextRequest) {
  const secret = sessionSecret();
  const session = await readSession(request.cookies.get(ADMIN_COOKIE)?.value, secret);

  if (!session) {
    return NextResponse.json({ signedIn: false }, { status: 200 });
  }

  return NextResponse.json({
    signedIn: true,
    email: session.email,
    expiresAt: session.exp,
  });
}
