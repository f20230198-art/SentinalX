import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCrtMode } from "../hooks/useCrtMode";
import { WatchIndicator } from "./WatchIndicator";
import { LanguageSwitcher } from "./LanguageSwitcher";

// The bracketed index ("[01]") is decorative chrome and stays fixed; only the
// label word is translated, via the i18n `key`.
const NAV = [
  { to: "/", index: "01", key: "nav.console" },
  { to: "/posts", index: "02", key: "nav.feed" },
  { to: "/techniques", index: "03", key: "nav.mitre" },
  { to: "/investigations", index: "04", key: "nav.investigations" },
  { to: "/scout", index: "05", key: "nav.scout" },
];

export function Header() {
  const [crt, toggleCrt] = useCrtMode();
  const { t } = useTranslation();
  return (
    <header className="relative z-10 border-b border-border-soft bg-base/80 backdrop-blur-sm">
      <div className="max-w-[1440px] mx-auto px-8 py-4 flex items-center gap-8">
        <Link to="/" className="font-display text-lg font-semibold tracking-tight">
          <span className="text-accent">SENTINEL</span>
          <span className="text-text">X</span>
          <span className="text-text-muted font-mono text-xs ml-2">// I</span>
        </Link>
        <nav className="flex gap-6 ml-8">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `font-mono text-[11px] tracking-[0.2em] transition-colors ${
                  isActive ? "text-accent" : "text-text-muted hover:text-text"
                }`
              }
            >
              [{n.index}] {t(n.key)}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <LanguageSwitcher />
          <WatchIndicator />
          <button
            onClick={toggleCrt}
            className={`font-mono text-[10px] tracking-[0.2em] border px-3 py-1.5 transition-colors ${
              crt
                ? "border-accent text-accent"
                : "border-border-soft text-text-muted hover:text-text"
            }`}
            aria-pressed={crt}
          >
            [ CRT ]
          </button>
        </div>
      </div>
    </header>
  );
}

export function SectionDivider({
  index,
  label,
  trailing,
}: {
  index: string;
  label: string;
  trailing?: string;
}) {
  return (
    <div className="section-divider my-12">
      <span className="text-accent">[{index}]</span>
      <span>// {label}</span>
      <hr />
      {trailing && <span className="text-text-muted">{trailing}</span>}
    </div>
  );
}
