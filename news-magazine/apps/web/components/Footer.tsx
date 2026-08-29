import Link from "next/link";

export function Footer() {
  return (
    <footer className="mt-16 border-t border-gray-200 bg-white">
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-8 text-sm text-gray-500 sm:flex-row sm:items-center sm:justify-between">
        <p>© {new Date().getFullYear()} Nepal News &amp; Magazine. All rights reserved.</p>
        <div className="flex flex-wrap gap-4">
          <Link href="/category/national" className="transition hover:text-gray-900">
            National
          </Link>
          <Link href="/category/politics" className="transition hover:text-gray-900">
            Politics
          </Link>
          <Link href="/category/business" className="transition hover:text-gray-900">
            Business
          </Link>
          <Link href="/category/technology" className="transition hover:text-gray-900">
            Technology
          </Link>
          <Link href="/category/sports" className="transition hover:text-gray-900">
            Sports
          </Link>
          <Link href="/category/entertainment" className="transition hover:text-gray-900">
            Entertainment
          </Link>
        </div>
      </div>
    </footer>
  );
}
