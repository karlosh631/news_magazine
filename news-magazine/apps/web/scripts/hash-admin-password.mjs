#!/usr/bin/env node
/**
 * Make a password hash for the admin panel.
 *
 * WHY THIS EXISTS
 * ---------------
 * The panel's password is stored as a PBKDF2 hash, and until now there
 * was no way to make a new one. The only hash in the repository is the
 * seeded one, whose plaintext is public - it is in this repository, in
 * a test file, and the hash is a constant the migration ships. A
 * credential that is published is not a credential, so the operator
 * needs a way to replace it, and replacing it means making a hash.
 *
 * USAGE
 * -----
 *   node scripts/hash-admin-password.mjs "the new password"
 *
 * It prints the hash and the SQL to store it. It prints the SQL
 * rather than running it: this script has no database access on
 * purpose, and the operator may want to paste the statement into
 * their own SQL editor and see what it does before it runs.
 *
 * The format is the one `lib/admin-auth.ts` verifies:
 *
 *   pbkdf2-sha256$<iterations>$<salt base64>$<hash base64>
 *
 * The iteration count travels WITH the hash rather than being read
 * from a constant, so raising it later does not invalidate every
 * existing password: a new hash is written with the new count and old
 * ones keep verifying against their own.
 */

import { webcrypto } from "node:crypto";

const PBKDF2_ITERATIONS = 210_000;
const SALT_BYTES = 16;
const KEY_BITS = 256;

const encoder = new TextEncoder();

function toBase64(bytes) {
  return Buffer.from(bytes).toString("base64");
}

/**
 * Derive the hash for a password.
 *
 * The salt is random every time, so the same password produces a
 * different hash each run. That is the point: a salt that is not
 * random is a salt that is not a salt.
 */
async function hashPassword(password) {
  const salt = webcrypto.getRandomValues(new Uint8Array(SALT_BYTES));

  const key = await webcrypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );

  const derived = await webcrypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations: PBKDF2_ITERATIONS, hash: "SHA-256" },
    key,
    KEY_BITS
  );

  return `pbkdf2-sha256$${PBKDF2_ITERATIONS}$${toBase64(salt)}$${toBase64(
    new Uint8Array(derived)
  )}`;
}

const password = process.argv[2];

if (!password) {
  console.error('Usage: node scripts/hash-admin-password.mjs "the new password"');
  process.exit(1);
}

if (password.length < 10) {
  console.error(
    "That password is shorter than ten characters. A password on a panel " +
      "that can read the whole database should not be short."
  );
  process.exit(1);
}

const hash = await hashPassword(password);

console.log("Hash:");
console.log(hash);
console.log("");
console.log("SQL:");
console.log("");
console.log("update admin_users");
console.log("set password_hash = '" + hash + "'");
console.log("where email = 'thapakarlosh631@gmail.com';");
console.log("");
console.log(
  "Run it, then sign in with the new password. The old one stops " +
    "working the moment the row is updated."
);
