import { expect, test } from "@playwright/test";

import {
  ADMIN_SESSION_SECONDS,
  DUMMY_PASSWORD_HASH,
  HASH_FORMAT_VERSION,
  PBKDF2_ITERATIONS,
  constantTimeStringEqual,
  hashNeedsUpgrade,
  hashPassword,
  readSession,
  readSessionWithSecrets,
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
 *   - the hash this library writes is one the library can read back;
 *   - a hash of one password never verifies another;
 *   - a cookie that was tampered with does not open the door;
 *   - a cookie that has expired does not open the door;
 *   - a cookie signed with a different secret does not open the door;
 *   - a cookie signed with the PREVIOUS secret still opens the door,
 *     so that a signing key can be replaced without signing everybody
 *     out at once.
 *
 * WHY THERE IS NO PASSWORD IN THIS FILE
 * -------------------------------------
 * This file used to contain the seeded account's password in
 * plaintext, so that it could prove "the migration's hash and this
 * library's verifier agree".
 *
 * That is a live credential written in a repository, and the
 * repository is published. A password in a public repository is not a
 * password - it is a published credential, and the seed's hash is
 * published beside it, so anybody can confirm a guess against it in
 * about a fifth of a second.
 *
 * So the password is gone from here, and the check it was there for
 * is kept in a form that does not need it:
 *
 *   - the seeded hash is checked for SHAPE (scheme, iteration count,
 *     salt length, key length), which is what would actually break
 *     the sign-in if it were wrong; and
 *   - the verifier is checked against hashes IT writes, with random
 *     salts, which is a stronger test of the verifier than one fixed
 *     password ever was.
 *
 * The seeded credential itself has to be replaced with one of the
 * operator's own:
 *
 *   cd apps/web
 *   node scripts/hash-admin-password.mjs "a long password of your own"
 *
 * and the SQL it prints run against the database. The account is then
 * moved onto a salt of its own the first time it signs in.
 */

const SECRET = "a-test-secret-that-is-long-enough-to-be-realistic";

const PREVIOUS_SECRET = "the-secret-that-was-in-use-yesterday-1234";

/**
 * The hash in supabase/migrations/0017_admin_users.sql.
 *
 * It is here so that the SHAPE of the seeded hash is pinned: if a
 * future edit changed the scheme, the iteration count, the salt
 * length or the key length, the operator's sign-in would stop
 * working and nothing else would say why.
 */
const SEEDED_HASH =
  "pbkdf2-sha256$210000$9eAfn0QvNAWZhY8Q+dtMiQ==$a2ZYetjYH2HHf35j749aVfGQpGOqEkjfUGBn/vseolw=";

test.describe("the seeded hash", () => {
  test("is shaped the way this library writes hashes", () => {
    const parts = SEEDED_HASH.split("$");
    expect(parts).toHaveLength(4);

    const scheme = parts[0] ?? "";
    const iterations = parts[1] ?? "";
    const salt = parts[2] ?? "";
    const key = parts[3] ?? "";

    expect(scheme).toBe("pbkdf2-sha256");

    // The iteration count travels with the hash, and it has to be one
    // this library is willing to spend the time on.
    expect(Number.parseInt(iterations, 10)).toBeGreaterThanOrEqual(100_000);

    // A 16 byte salt and a 32 byte key.
    expect(Buffer.from(salt, "base64")).toHaveLength(16);
    expect(Buffer.from(key, "base64")).toHaveLength(32);
  });

  test("does not accept an empty or an arbitrary password", async () => {
    expect(await verifyPassword("", SEEDED_HASH)).toBe(false);
    expect(await verifyPassword("not the password", SEEDED_HASH)).toBe(false);
  });

  test("is not the same record as the one used for unknown accounts", () => {
    // The dummy hash that stands in for "no such account" used to be
    // the seeded operator's own hash, which put a live credential in
    // a second place and meant the "unknown account" path was doing
    // exactly the same work as the "known account" path for a reason
    // that had nothing to do with timing.
    expect(DUMMY_PASSWORD_HASH).not.toBe(SEEDED_HASH);
  });

  test("and the dummy costs exactly the same to verify", () => {
    // A dummy that cost a different amount of work from a real
    // comparison would answer "no such account" at a different speed
    // from "wrong password", and the whole point of it would be lost.
    const dummy = DUMMY_PASSWORD_HASH.split("$");
    const seeded = SEEDED_HASH.split("$");

    expect(dummy[0]).toBe(seeded[0]);
    expect(dummy[1]).toBe(seeded[1]);
    expect(dummy[1]).toBe(String(PBKDF2_ITERATIONS));
  });
});

test.describe("the hashes this library writes", () => {
  test("a password verifies against its own hash", async () => {
    const hash = await hashPassword("correct horse battery staple");
    expect(await verifyPassword("correct horse battery staple", hash)).toBe(true);
  });

  test("another password does not", async () => {
    const hash = await hashPassword("correct horse battery staple");
    expect(await verifyPassword("correct horse battery stapl", hash)).toBe(false);
    expect(await verifyPassword("Correct horse battery staple", hash)).toBe(false);
    expect(await verifyPassword("", hash)).toBe(false);
  });

  test("the same password twice gives two different hashes", async () => {
    // A salt that is not random is not a salt. Two accounts with the
    // same password must not look the same in the table.
    const first = await hashPassword("the same password");
    const second = await hashPassword("the same password");

    expect(first).not.toBe(second);
    expect(await verifyPassword("the same password", first)).toBe(true);
    expect(await verifyPassword("the same password", second)).toBe(true);
  });

  test("the hash carries the iteration count it was made with", async () => {
    const hash = await hashPassword("a password");
    const parts = hash.split("$");

    const scheme = parts[0] ?? "";
    const iterations = parts[1] ?? "";
    const salt = parts[2] ?? "";
    const key = parts[3] ?? "";

    expect(scheme).toBe("pbkdf2-sha256");
    expect(Number.parseInt(iterations, 10)).toBe(PBKDF2_ITERATIONS);
    expect(Buffer.from(salt, "base64")).toHaveLength(16);
    expect(Buffer.from(key, "base64")).toHaveLength(32);
  });

  test("a missing or malformed hash is refused rather than accepted", async () => {
    expect(await verifyPassword("a password", null)).toBe(false);
    expect(await verifyPassword("a password", undefined)).toBe(false);
    expect(await verifyPassword("a password", "")).toBe(false);
    expect(await verifyPassword("a password", "not-a-hash")).toBe(false);
    expect(await verifyPassword("a password", "bcrypt$1$2$3")).toBe(false);
    // A record that is ours in shape but not in its parts.
    expect(await verifyPassword("a password", "pbkdf2-sha256$210000$c2FsdA==$aGFzaA==")).toBe(
      false
    );
  });
});

test.describe("the hash version", () => {
  test("a hash written under an older version needs upgrading", () => {
    // The seeded hash has a salt that is published. The first sign-in
    // after the upgrade rewrites it with a salt of its own.
    expect(hashNeedsUpgrade(SEEDED_HASH, 1)).toBe(true);
  });

  test("a hash written under the current version does not", async () => {
    const hash = await hashPassword("a password");
    expect(hashNeedsUpgrade(hash, HASH_FORMAT_VERSION)).toBe(false);
  });

  test("a row whose version is unknown is left alone", () => {
    // The version column arrived with a migration. A deployment that
    // has not applied it must not have its hash rewritten on every
    // sign-in.
    expect(hashNeedsUpgrade(SEEDED_HASH, null)).toBe(false);
    expect(hashNeedsUpgrade(SEEDED_HASH, undefined)).toBe(false);
  });

  test("a hash made with an older iteration count needs upgrading", () => {
    const older = "pbkdf2-sha256$1000$9eAfn0QvNAWZhY8Q+dtMiQ==$a2ZYetjYH2HHf35j749aVfGQpGOqEkjfUGBn/vseolw=";
    expect(hashNeedsUpgrade(older, HASH_FORMAT_VERSION)).toBe(true);
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

test.describe("rotating the signing key", () => {
  const session = {
    uid: "0f7c1a2b-3d4e-5f60-7182-93a4b5c6d7e8",
    email: "thapakarlosh631@gmail.com",
    sid: "11111111-2222-3333-4444-555555555555",
    exp: Math.floor(Date.now() / 1000) + ADMIN_SESSION_SECONDS,
  };

  test("a cookie signed with the previous key is still accepted", async () => {
    const cookie = await signSession(session, PREVIOUS_SECRET);
    const read = await readSessionWithSecrets(cookie, [SECRET, PREVIOUS_SECRET]);

    expect(read).not.toBeNull();
    expect(read?.uid).toBe(session.uid);
  });

  test("a cookie signed with the current key is accepted", async () => {
    const cookie = await signSession(session, SECRET);
    const read = await readSessionWithSecrets(cookie, [SECRET, PREVIOUS_SECRET]);

    expect(read).not.toBeNull();
    expect(read?.uid).toBe(session.uid);
  });

  test("a cookie signed with neither key is refused", async () => {
    const cookie = await signSession(session, "some-other-secret-entirely");
    expect(
      await readSessionWithSecrets(cookie, [SECRET, PREVIOUS_SECRET])
    ).toBeNull();
  });

  test("no keys means no session", async () => {
    const cookie = await signSession(session, SECRET);
    expect(await readSessionWithSecrets(cookie, [])).toBeNull();
  });
});

test.describe("comparing secrets", () => {
  test("the same string equals itself", () => {
    expect(constantTimeStringEqual("a-secret", "a-secret")).toBe(true);
  });

  test("a different string does not", () => {
    expect(constantTimeStringEqual("a-secret", "a-secrets")).toBe(false);
    expect(constantTimeStringEqual("a-secret", "a-secre")).toBe(false);
    expect(constantTimeStringEqual("a-secret", "b-secret")).toBe(false);
  });

  test("an empty string is not a secret", () => {
    expect(constantTimeStringEqual("", "")).toBe(true);
    expect(constantTimeStringEqual("a-secret", "")).toBe(false);
  });
});
