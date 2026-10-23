"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

/**
 * The sign-in form.
 *
 * WHY IT IS A SEPARATE COMPONENT
 * ------------------------------
 * The `redirect` target arrives as a PROP rather than through
 * `useSearchParams`. `useSearchParams` makes a page opt out of static
 * rendering, and Next.js refuses to build such a page without a
 * Suspense boundary around it — so the alternative is a page that
 * cannot be built at all, or a `<Suspense>` wrapper whose fallback
 * flashes on every visit. Reading the parameter in the server
 * component and passing it down is both simpler and correct: the
 * value is validated there too, which is where the open-redirect
 * check belongs.
 *
 * ACCESSIBILITY
 * -------------
 * The site scores 100 on Lighthouse accessibility and this page is
 * not allowed to be the thing that takes that away:
 *
 *   - the fields are labelled, and the labels are visible;
 *   - the error is announced, with role="alert" and aria-live, so a
 *     screen reader says why the sign-in failed rather than leaving
 *     the reader guessing;
 *   - the error is tied to the form with aria-describedby, so it is
 *     read out when focus lands on the field that caused it;
 *   - the button says what it is doing while it is doing it, and is
 *     disabled while it is, so it cannot be submitted twice;
 *   - the focus moves to the error, so a keyboard user is told what
 *     happened without having to hunt for it.
 */

type Status = "idle" | "submitting" | "error";

export function LoginForm({ redirect }: { redirect: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const errorRef = useRef<HTMLParagraphElement | null>(null);

  // The error is announced when it appears, and the focus goes to it,
  // so neither a screen reader nor a keyboard user is left wondering.
  useEffect(() => {
    if (status === "error" && errorRef.current) {
      errorRef.current.focus();
    }
  }, [status]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (status === "submitting") return;

    setStatus("submitting");
    setMessage("");

    try {
      const response = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, redirect }),
      });

      const payload = (await response.json()) as {
        success?: boolean;
        redirect?: string;
        error?: { message?: string };
      };

      if (!response.ok || !payload.success) {
        setMessage(
          payload.error?.message ?? "That email and password do not match."
        );
        setStatus("error");
        return;
      }

      // A full navigation, not a client-side push: the dashboard is a
      // server component that reads the cookie, and the client router
      // would render it before the cookie had been applied.
      window.location.href = payload.redirect ?? "/admin";
    } catch {
      setMessage("The sign-in service could not be reached. Try again shortly.");
      setStatus("error");
    }
  }

  return (
    <div className="w-full max-w-sm">
      <Link
        href="/"
        /*
          48 pixels tall.

          "Back to the news" is a link with a word in it and nothing
          else around it, so it was as small as the text in it - 20
          pixels, which is a line of type rather than a target. The
          padding is what makes it something a finger can hit.
        */
        className="mb-6 inline-flex min-h-[48px] items-center gap-2 text-sm text-gray-500 transition-colors hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 dark:text-gray-400 dark:hover:text-gray-100"
      >
        <span aria-hidden="true">←</span> Back to the news
      </Link>

      <div className="rounded-xl border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-700 dark:bg-gray-900">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
          Sign in
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          The admin dashboard for this site.
        </p>

        <form
          onSubmit={onSubmit}
          className="mt-6 space-y-4"
          aria-describedby={status === "error" ? "signin-error" : undefined}
        >
          <div>
            <label
              htmlFor="admin-email"
              className="block text-sm font-medium text-gray-700 dark:text-gray-300"
            >
              Email
            </label>
            <input
              id="admin-email"
              name="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={status === "error" ? true : undefined}
              className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-gray-900 focus:ring-1 focus:ring-gray-900 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100 dark:focus:border-gray-300 dark:focus:ring-gray-300"
            />
          </div>

          <div>
            <label
              htmlFor="admin-password"
              className="block text-sm font-medium text-gray-700 dark:text-gray-300"
            >
              Password
            </label>
            <input
              id="admin-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={status === "error" ? true : undefined}
              className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-gray-900 focus:ring-1 focus:ring-gray-900 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100 dark:focus:border-gray-300 dark:focus:ring-gray-300"
            />
          </div>

          {/*
            The error is a paragraph with role="alert" so it is
            announced the moment it appears, and it takes focus so a
            keyboard user is taken to it rather than having to find
            it. tabIndex={-1} makes it focusable without putting it
            in the tab order, which is what a static message must not
            be.
          */}
          {status === "error" && message ? (
            <p
              id="signin-error"
              ref={errorRef}
              role="alert"
              tabIndex={-1}
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 outline-none focus:ring-2 focus:ring-red-400 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
            >
              {message}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={status === "submitting"}
            /*
              48 pixels tall.

              This is the button the operator presses with a finger,
              and at `py-2.5` it was 40 pixels. It is the full width
              of the form, so the extra height costs nothing.
            */
            className="min-h-[48px] w-full rounded-md bg-gray-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-900 focus-visible:ring-offset-2 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white dark:focus-visible:ring-gray-100 dark:focus-visible:ring-offset-gray-900"
          >
            {status === "submitting" ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
