import Link from "next/link";

const NAV = [
  { href: "/category/national", label: "National" },
  { href: "/category/politics", label: "Politics" },
  { href: "/category/business", label: "Business" },
  { href: "/category/technology", label: "Technology" },
  { href: "/category/sports", label: "Sports" },
  { href: "/category/entertainment", label: "Entertainment" },
  { href: "/search", label: "Search" },
];

export function Header() {
  return (
    <header className="border-b">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
        <Link
          href="/"
          className="text-2xl font-headline font-bold"
        >
          Nepal News & Magazine
        </Link>

        <nav
          className="hidden md:flex gap-6 text-sm font-medium"
          aria-label="Main navigation"
        >
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="hover:underline"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
