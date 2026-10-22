import { expect, test } from "@playwright/test";

import {
  ADMIN_SESSION_SECONDS,
  readSession,
  signSession,
  verifyPassword,
} from "../lib/admin-auth";

/**
 * The admin sign-in.
 *
 * WHY THESE EXIST
 * ---------------
 * The panel is the one place on the site where a mistake is a security
 * mistake rather than a broken picture. The rules that matter are:
 *
 *   - the seeded hash accepts the operator's password and nothing else;
 *   - a cookie that was tampered with does not open the door;
 *   - a cookie that has expired does not open the door;
 *   - a cookie signed with a different secret does not open the door.
 *
 * The hash below is the one in supabase/migrations/0017_admin_users.sql.
 * If the migration's hash and this library's verifier ever disagree,
 * the operator cannot sign in and nothing else reports it - so the
 * hash is pinned here as well as in the migration.
 */

const SECRET = "a-test-secret-that-is-long-enough-to-be-realistic";

const SEEDED_HASH =
  "pbkdf2-sha256$210000$9eAfn0QvNAWZhY8Q+dtMiQ==$a2ZYetjYH2HHf35j749aVfGQpGOqEkjfUGBn/vseolw=";

test.describe("the seeded password", () => {
  test("the operator's password verifies", async () => {
    expect(await verifyPassword("new24hr", SEEDED_HASH)).toBe(true);
  });

  test("a wrong password does not", async () => {
    expect(await verifyPassword("new24hr ", SEEDED_HASH)).toBe(false);
    expect(await verifyPassword("New24hr", SEEDED_HASH)).toBe(false);
    expect(await verifyPassword("", SEEDED_HASH)).toBe(false);
    expect(await verifyPassword("new24hrx", SEEDED_HASH)).toBe(false);
  });

  test("a missing or malformed hash is refused rather than accepted", async () => {
    expect(await verifyPassword("new24hr", null)).toBe(false);
    expect(await verifyPassword("new24hr", undefined)).toBe(false);
    expect(await verifyPassword("new24hr", "")).toBe(false);
    expect(await verifyPassword("new24hr", "not-a-hash")).toBe(false);
    expect(await verifyPassword("new24hr", "bcrypt$1$2$3")).toBe(false);
  });

  test("the hash is not the password", () => {
    // The plaintext must not be recoverable from what is stored.
    expect(SEEDED_HASH).not.toContain("new24hr");
  });
});

test.describe("the session cookie", () => {
  const session = {
    uid: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
    email: "thapakarlosh631@gmail.com",
    sid: "11111111-2222-3333-4444-555555555555",
    exp: Math.floor(Date.now() / 1000) + ADMIN_SESSION_SECONDS,
  };

  test("a signed session round-trips", async () => {
    const cookie = await signSession(session, SECRET);
    const read = await readSession(cookie, SECRET);

    expect(read).not.toBeNull();
    expect(read?.uid).toBe(session.uid);
    expect(read?.email).toBe(session.email);
    expect(read?.sid).toBe(session.sid);
  });

  test("an email address containing dots still round-trips", async () => {
    // The payload carries the email address, and an email address
    // contains dots - "thapakarlosh631@gmail.com" has one. Splitting
    // the payload on "." broke every real session; the delimiter is
    // now a character that cannot occur in an address, and this test
    // is here so that it cannot be put back.
    const dotted = {
      ...session,
      email: "first.last+tag@mail.example.co.uk",
    };

    const cookie = await signSession(dotted, SECRET);
    const read = await readSession(cookie, SECRET);

    expect(read).not.toBeNull();
    expect(read?.email).toBe("first.last+tag@mail.example.co.uk");
    expect(read?.uid).toBe(dotted.uid);
    expect(read?.sid).toBe(dotted.sid);
  });

  test("a tampered payload is refused", async () => {
    const cookie = await signSession(session, SECRET);
    // Swap the user id for somebody else's.
    const [encoded, signature] = cookie.split(".");
    const forged = Buffer.from(
      `11111111-2222-3333-4444-555555555555.${session.email}.${session.sid}.${session.exp}`,
      "utf-8"
    )
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

    expect(await readSession(`${forged}.${signature}`, SECRET)).toBeNull();
    expect(encoded).not.toBe(forged);
  });

  test("a cookie signed with another secret is refused", async () => {
    const cookie = await signSession(session, SECRET);
    expect(await readSession(cookie, "a-completely-different-secret")).toBeNull();
  });

  test("an expired session is refused", async () => {
    const expired = {
      ...session,
      exp: Math.floor(Date.now() / 1000) - 60,
    };
    const cookie = await signSession(expired, SECRET);
    expect(await readSession(cookie, SECRET)).toBeNull();
  });

  test("a malformed cookie is refused", async () => {
    expect(await readSession("", SECRET)).toBeNull();
    expect(await readSession(null, SECRET)).toBeNull();
    expect(await readSession("not-a-cookie", SECRET)).toBeNull();
    expect(await readSession("only.two", SECRET)).toBeNull();
  });

  test("no secret means no session", async () => {
    const cookie = await signSession(session, SECRET);
    expect(await readSession(cookie, null)).toBeNull();
  });
});
