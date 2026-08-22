import Link from "next/link";

const navigation = [
  {
    label: "National",
    href: "/category/national",
  },
  {
    label: "Politics",
    href: "/category/politics",
  },
  {
    label: "Business",
    href: "/category/business",
  },
  {
    label: "Technology",
    href: "/category/technology",
  },
  {
    label: "Sports",
    href: "/category/sports",
  },
  {
    label: "Entertainment",
    href: "/category/entertainment",
  },
];

export function Header() {
  return (
    <header className="sticky top-0 z-50 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        {/* Site Logo / Home */}
        <Link
          href="/"
          className="shrink-0 text-xl font-bold tracking-tight font-headline sm:text-2xl"
          aria-label="Nepal News & Magazine - Home"
        >
          Nepal News & Magazine
        </Link>

        {/* Desktop Navigation */}
        <nav
          className="hidden items-center gap-5 text-sm font-medium md:flex"
          aria-label="Main navigation"
        >
          {navigation.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="transition-colors hover:text-gray-600"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        {/* Mobile Navigation */}
        <nav
          className="flex items-center md:hidden"
          aria-label="Mobile navigation"
        >
          <details className="relative">
            <summary className="cursor-pointer list-none rounded-md border px-3 py-2 text-sm font-medium hover:bg-muted">
              Menu
            </summary>

            <div className="absolute right-0 top-full mt-2 min-w-52 rounded-lg border bg-background p-2 shadow-lg">
              {navigation.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="block rounded-md px-3 py-2 text-sm font-medium hover:bg-muted"
                >
                  {item.label}
                </Link>
              ))}
            </div>
          </details>
        </nav>
      </div>
    </header>
  );
}
