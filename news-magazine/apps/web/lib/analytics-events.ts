import { createServiceRoleClient } from "@/lib/supabase/server";

/**
 * Record something a reader did.
 *
 * WHY THIS EXISTS
 * ---------------
 * The `analytics_events` table had an index, an insert-only RLS policy
 * and a panel on the admin dashboard, and NOTHING EVER WROTE TO IT.
 * The panel said "No reader events recorded yet" and would have said
 * it for ever, which is worse than not having the panel: it teaches
 * the operator to ignore a number that is not real.
 *
 * So the events are recorded here, in one place, so that every
 * surface that can report a reader doing something uses the same
 * rules.
 *
 * WHY THE SERVICE-ROLE KEY AND NOT THE ANON KEY
 * ---------------------------------------------
 * The anon key was supposed to be enough: the table's policy is
 * insert-only for everyone and there is no SELECT policy, so a
 * reader's client could have written this itself.
 *
 * It was not enough, and the failure was invisible. The write was
 * rejected by the database — the policy that permits it is created in
 * 0002 with a bare `create policy` and no `drop policy if exists` in
 * front of it, so that file cannot be re-applied, and the operator's
 * migration runner stops at the first error in a file. A run that
 * stopped anywhere above the analytics_events policies left the
 * insert policy uncreated, and every write to the table has been
 * rejected since. The only trace was a line in the function log.
 *
 * The service role bypasses RLS and grants, so the write lands
 * whatever state the policy is in, and the policy still says "anyone
 * may insert", so nothing is widened by this. The key never reaches
 * the browser: this module is server-only.
 *
 * THE WRITE IS AWAITED, BUT IT NEVER THROWS
 * -----------------------------------------
 * The insert used to be fire-and-forget — the promise was not
 * awaited, because the request that triggered it should not wait on a
 * row that nothing reads in that request. On Vercel the invocation
 * can be frozen once the response is sent, so an unawaited write is
 * not guaranteed to complete: the event was simply lost, silently,
 * on every request that happened to end first.
 *
 * It is awaited now, and it still never throws. A reader who has
 * their view counted must never be shown an error because the
 * measurement failed, and a search must never fail because its event
 * did not land. So both failures are caught and logged, and the
 * caller carries on.
 */
export async function recordEvent(
  eventType: string,
  options: {
    articleId?: string | null;
    sessionId?: string | null;
    metadata?: Record<string, unknown> | null;
    /** Anything else worth recording, folded into the metadata. */
    [key: string]: unknown;
  } = {}
): Promise<void> {
  try {
    const supabase = createServiceRoleClient();
    const { articleId, sessionId, metadata, ...rest } = options;

    const { error } = await supabase
      .from("analytics_events")
      .insert({
        event_type: eventType,
        article_id: articleId ?? null,
        session_id: sessionId ?? null,
        metadata: {
          ...(metadata ?? {}),
          ...(Object.keys(rest).length ? rest : {}),
        },
      });

    if (error) {
      console.error(
        `[analytics] could not record the ${eventType} event:`,
        error.message
      );
    }
  } catch (error) {
    console.error(`[analytics] could not record the ${eventType} event:`, error);
  }
}
