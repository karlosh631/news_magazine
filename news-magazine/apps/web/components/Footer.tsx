/**
 * Footer.
 *
 * Stage 1 repeated the same six hardcoded category links here. They
 * duplicated the header nav, wrapped onto two lines on desktop, and
 * still went stale the moment a category was added. The header carries
 * navigation; the footer carries the credit.
 */
export function Footer() {
  return (
    <footer className="mt-16 border-t border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
      <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-2 px-4 py-8 text-sm text-gray-500 sm:flex-row dark:text-gray-400">
        <p>© {new Date().getFullYear()} Nepal News &amp; Magazine. All rights reserved.</p>
        <p>
          By{" "}
          <span className="font-semibold text-gray-700 dark:text-gray-200">
            Karlosh Thapa
          </span>
        </p>
      </div>
    </footer>
  );
}