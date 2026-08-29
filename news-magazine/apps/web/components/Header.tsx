"use client";



import Link from "next/link";

import { useEffect, useRef, useState } from "react";

import { useRouter } from "next/navigation";



// Keep this SVG identical to app/icon.svg (Next.js's auto-favicon file

// convention) so the navbar icon and the browser tab icon match.

const CATEGORIES = [

  { label: "National", slug: "national" },

  { label: "Politics", slug: "politics" },

  { label: "Business", slug: "business" },

  { label: "Technology", slug: "technology" },

  { label: "Sports", slug: "sports" },

  { label: "Entertainment", slug: "entertainment" },

];



const AUTO_REFRESH_MS = 60_000; // 1 minute, applies to every page via this shared Header



function LogoIcon() {

  return (

    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" className="shrink-0" aria-hidden="true">

      <rect x="2" y="4" width="20" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />

      <path d="M6 8h6M6 11h6M6 14h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />

      <rect x="14" y="8" width="4" height="4" rx="0.5" stroke="currentColor" strokeWidth="1.4" />

      <path d="M14 15h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />

    </svg>

  );

}



function ReloadIcon({ spinning }: { spinning: boolean }) {

  return (

    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className={spinning ? "animate-spin" : ""} aria-hidden="true">

      <path d="M20 11a8 8 0 1 0-2.34 5.66" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />

      <path d="M20 5v6h-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />

    </svg>

  );

}



export function Header() {

  const router = useRouter();

  const [menuOpen, setMenuOpen] = useState(false);

  const [reloading, setReloading] = useState(false);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);



  const doRefresh = () => {

    setReloading(true);

    router.refresh(); // re-runs server-component data fetching on whatever page you're on

    window.setTimeout(() => setReloading(false), 700);

  };



  useEffect(() => {

    intervalRef.current = setInterval(doRefresh, AUTO_REFRESH_MS);

    return () => {

      if (intervalRef.current) clearInterval(intervalRef.current);

    };

    // eslint-disable-next-line react-hooks/exhaustive-deps

  }, []);



  return (

    // Solid white on mobile (no transparency/blur) per your request.

    // md:bg-white/90 + md:backdrop-blur only kicks in at desktop widths.

    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white md:bg-white/90 md:backdrop-blur">

      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4">

        <Link

          href="/"

          className="flex items-center gap-2 font-bold text-gray-900 transition hover:text-blue-600 sm:text-xl"

        >

          <LogoIcon />

          <span className="truncate">Nepal News &amp; Magazine</span>

        </Link>



        <nav className="hidden items-center gap-1 md:flex">

          {CATEGORIES.map((cat) => (

            <Link

              key={cat.slug}

              href={`/category/${cat.slug}`}

              className="rounded-md px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100 hover:text-black"

            >

              {cat.label}

            </Link>

          ))}

        </nav>



        <div className="flex items-center gap-2">

          <button

            type="button"

            onClick={doRefresh}

            aria-label="Reload latest news"

            className="flex h-9 w-9 items-center justify-center rounded-full border border-gray-300 text-gray-700 transition hover:border-black hover:bg-gray-900 hover:text-white active:scale-95"

          >

            <ReloadIcon spinning={reloading} />

          </button>



          <button

            type="button"

            onClick={() => setMenuOpen((v) => !v)}

            aria-label="Toggle menu"

            aria-expanded={menuOpen}

            className="flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 text-gray-700 transition hover:bg-gray-100 md:hidden"

          >

            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">

              {menuOpen ? (

                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />

              ) : (

                <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />

              )}

            </svg>

          </button>

        </div>

      </div>



      {/* MOBILE DROPDOWN — solid white, never transparent */}

      {menuOpen && (

        <nav className="flex flex-col gap-1 border-t border-gray-200 bg-white px-4 py-3 md:hidden">

          {CATEGORIES.map((cat) => (

            <Link

              key={cat.slug}

              href={`/category/${cat.slug}`}

              onClick={() => setMenuOpen(false)}

              className="rounded-md px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"

            >

              {cat.label}

            </Link>

          ))}

        </nav>

      )}

    </header>

  );

} 

