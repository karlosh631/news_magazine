import { createPublicSupabaseClient } from "@/lib/supabase/server";

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
 * THE INSERT IS FIRE-AND-FORGET
 * -----------------------------
 * An event is a measurement, not a feature. A reader who has their
 * view counted must never be shown an error because the measurement
 * failed, and a search must never fail because its event did not
 * land. So this never throws and never rejects: it logs and moves on.
 *
 * The insert is fire-and-forget at the CALLER's end too - the promise
 * is not awaited - because the request that triggered it should not
 * wait on a row that nothing reads in that request.
 *
 * THE ANON KEY IS ENOUGH
 * -----------------------
 * The table's policy is insert-only for everyone and there is no
 * SELECT policy, so a reader's client could have written this itself.
 * It is done on the server instead, because the server is where the
 * session id and the referrer are, and because a client that cannot
 * be trusted with a write volume is better not asked to make one.
 */
export function recordEvent(
  eventType: string,
  options: {
    articleId?: string | null;
    sessionId?: string | null;
    metadata?: Record<string, unknown> | null;
    /** Anything else worth recording, folded into the metadata. */
    [key: string]: unknown;
  } = {}
): void {
  try {
    const supabase = createPublicSupabaseClient();
    const { articleId, sessionId, metadata, ...rest } = options;

    void Promise.resolve(
      supabase
        .from("analytics_events")
        .insert({
          event_type: eventType,
          article_id: articleId ?? null,
          session_id: sessionId ?? null,
          metadata: {
            ...(metadata ?? {}),
            ...(Object.keys(rest).length ? rest : {}),
          },
        })
    )
      .then(({ error }) => {
        if (error) {
          console.error(
            `[analytics] could not record the ${eventType} event:`,
            error.message
          );
        }
      })
      .catch(() => undefined);
  } catch (error) {
    console.error(`[analytics] could not record the ${eventType} event:`, error);
  }
}
