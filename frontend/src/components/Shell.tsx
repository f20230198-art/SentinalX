import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { WatchIndicator } from "./WatchIndicator";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { Shortcuts } from "./Shortcuts";
import { AlertBadge } from "./AlertBadge";

// Top menu links (labels come from the translation files)
const NAV = [
  { to: "/", key: "nav.console" },
  { to: "/posts", key: "nav.feed" },
  { to: "/techniques", key: "nav.mitre" },
  { to: "/investigations", key: "nav.investigations" },
  { to: "/discover", key: "nav.discover" },
  { to: "/scout", key: "nav.scout" },
];

// Sticky top bar: logo, menu, then language / alerts / status / shortcuts
export function Header() {
  const { t } = useTranslation();
  return (
    <header className="sticky top-0 z-30 border-b-2 border-rule bg-surface-1">
      <div className="mx-auto flex max-w-[1440px] items-center gap-x-8 px-4 sm:px-8">
        <Link to="/" className="py-3 text-lg font-extrabold tracking-[-0.03em] text-text no-underline">
          SentinelX
        </Link>
        {/* Scrolls horizontally on small screens instead of overflowing. */}
        <nav aria-label="Primary" className="-mb-0.5 flex min-w-0 flex-1 gap-6 overflow-x-auto">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `whitespace-nowrap border-b-2 py-3.5 text-sm font-semibold no-underline transition-colors ${
                  isActive
                    ? "border-accent text-text"
                    : "border-transparent text-text-muted hover:text-text"
                }`
              }
            >
              {t(n.key)}
            </NavLink>
          ))}
        </nav>
        {/* Right-side tools (hidden on small screens) */}
        <div className="hidden items-center gap-3 md:flex">
          <LanguageSwitcher />
          <AlertBadge />
          <WatchIndicator />
          <Shortcuts />
        </div>
      </div>
    </header>
  );
}

/** Section heading: thick rule, title left, extra info right (`index` is unused). */
export function SectionDivider({
  label,
  trailing,
}: {
  index?: string;
  label: string;
  trailing?: string;
}) {
  return (
    <div className="section-divider mt-14 mb-6">
      <h2>{label}</h2>
      {trailing && <span className="meta">{trailing}</span>}
    </div>
  );
}
