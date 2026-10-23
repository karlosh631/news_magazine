/**
 * Is this session still real?
 *
 * THE COOKIE IS A CLAIM, THE ROW IS THE FACT
 * ------------------------------------------
 * The cookie carries a signed session id. The signature says the
 * cookie was issued by this application and has not expired; it does
 * NOT say the session still exists. Deleting the row in
 * `admin_sessions` is what ends a session immediately, and it is the
 * only way to end one everywhere at once.
 *
 * So the two are checked together, in one place, and every route that
 * trusts the cookie goes through here. A route that checks only the
 * signature is a route where "sign out everywhere" does not work.
 *
 * WHAT IS CHECKED
 * ---------------
 *   - the cookie is signed with a key in use, and has not expired;
 *   - the session row exists, and it belongs to the same admin the
 *     cookie names;
 *   - the session row has not expired either;
 *   - the account is still ACTIVE, so deactivating an admin ends
 *     their sessions without waiting for the cookie to run out.
 *
 * IT FAILS CLOSED
 * ---------------
 * If the tables cannot be read, the answer is no. A check that says
 * yes when it cannot tell is not a check.
 *
 * This module is SERVER ONLY. It uses the service role key, which
 * bypasses row level security and must never reach a browser, and it
 * is not imported by `middleware.ts` - the middleware runs in the Edge
 * runtime and only checks that the cookie is well formed.
 */

import {
  ADMIN_COOKIE,
  readSessionWithSecrets,
  sessionSecrets,
  type AdminSession,
} from "@/lib/admin-auth";
import { createServiceRoleClient } from "@/lib/supabase/server";

/** Why a session was not accepted. For the log, never for the reader. */
export type AdminSessionFailure =
  | "no-cookie"
  | "not-signed"
  | "no-session-row"
  | "wrong-owner"
  | "session-expired"
  | "inactive"
  | "lookup-failed";

/** The result of checking a cookie. */
export type AdminSessionCheck =
  | { ok: true; session: AdminSession }
  | { ok: false; reason: AdminSessionFailure };

/**
 * Check the cookie a request carries.
 *
 * `cookieValue` is the raw cookie, so this can be used from a route
 * handler (`request.cookies.get(...)`) and from a server component
 * (`(await cookies()).get(...)`) without either of them knowing
 * about the other.
 */
export async function checkAdminSession(
  cookieValue: string | null | undefined
): Promise<AdminSessionCheck> {
  if (!cookieValue) return { ok: false, reason: "no-cookie" };

  const session = await readSessionWithSecrets(cookieValue, sessionSecrets());
  if (!session) return { ok: false, reason: "not-signed" };

  try {
    const supabase = createServiceRoleClient();

    const { data: row, error } = await supabase
      .from("admin_sessions")
      .select("id, admin_user_id, expires_at")
      .eq("id", session.sid)
      .maybeSingle();

    if (error) {
      console.error("[admin] could not read the session row:", error.message);
      return { ok: false, reason: "lookup-failed" };
    }

    if (!row) return { ok: false, reason: "no-session-row" };

    // The row has to belong to the admin the cookie names. The
    // signature already prevents the cookie being forged, so a
    // mismatch here means the row and the cookie disagree, and the
    // cookie is not the one to believe.
    if ((row as { admin_user_id: string }).admin_user_id !== session.uid) {
      return { ok: false, reason: "wrong-owner" };
    }

    const expiresAt = new Date(
      (row as { expires_at: string }).expires_at
    ).getTime();
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
      return { ok: false, reason: "session-expired" };
    }

    const { data: admin, error: adminError } = await supabase
      .from("admin_users")
      .select("is_active")
      .eq("id", session.uid)
      .maybeSingle();

    if (adminError) {
      console.error(
        "[admin] could not read the admin row:",
        adminError.message
      );
      return { ok: false, reason: "lookup-failed" };
    }

    if (!admin || !(admin as { is_active: boolean }).is_active) {
      return { ok: false, reason: "inactive" };
    }

    return { ok: true, session };
  } catch (error) {
    console.error("[admin] the session could not be checked:", error);
    return { ok: false, reason: "lookup-failed" };
  }
}

/**
 * The cookie name, re-exported so a caller that has the session
 * module does not also have to import the auth module.
 */
export { ADMIN_COOKIE };
