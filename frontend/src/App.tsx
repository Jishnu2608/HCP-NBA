import {
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  LogOut,
  Menu,
  ShieldCheck,
  X,
} from "lucide-react";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { gsap, playExit, reducedMotion, useGSAP } from "./motion";
import type { ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, matchPath, useLocation, useNavigate } from "react-router-dom";
import { ROLE_LABEL, useAuth } from "./auth";
import AccessDenied from "./pages/AccessDenied";
import { BrandMark } from "./pages/AuthLayout";
import { AppErrorBoundary, ErrorScreen } from "./pages/ErrorPage";
import Invite from "./pages/Invite";
import LegalPage from "./pages/LegalPage";
import Reaccept from "./pages/Reaccept";

const RestrictedPrivacy = lazy(() => import("./pages/Privacy"));
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import VerifyOtp from "./pages/VerifyOtp";
import type { Permission } from "./permissions";
import { useAttention } from "./attention";
import { NAV_GROUPS, ROUTES, groupOf } from "./routes";
import { LegalLinks } from "./legal";
import { preferences } from "./session";
import { ThemeToggle } from "./theme";
import { Avatar, IconButton, Loading, PersonName, cx } from "./ui";

// The landing page carries the scroll-storytelling code (ScrollTrigger); it loads on its own.
const Landing = lazy(() => import("./pages/Landing"));

const PUBLIC_PATHS = ["/", "/login", "/signup", "/signup/verify"];

/** True for an address that is a real page of the signed-in app (so a signed-out visitor
 *  should sign in), as opposed to an address that does not exist at all (404). */
const isAppPath = (path: string) => path === "/denied" || ROUTES.some((r) => matchPath(r.path, path));

/** Menu entries come from the route table, filtered by the account's permissions. */
/**
 * One indicator for the whole menu that glides to the active destination, so a change of
 * page reads as a move rather than one marker vanishing and another appearing. Position is
 * read once per navigation (no layout reads while animating) and moved with quickTo.
 */
function useNavIndicator(list: React.RefObject<HTMLDivElement | null>, bar: React.RefObject<HTMLSpanElement | null>, deps: unknown[]) {
  const placed = useRef(false);
  useGSAP(
    () => {
      const root = list.current;
      const el = bar.current;
      if (!root || !el) return;
      const active = root.querySelector<HTMLElement>('a[aria-current="page"]');
      if (!active) {
        gsap.set(el, { opacity: 0 });
        placed.current = false;
        return;
      }
      const y = active.offsetTop + 8;
      const height = active.offsetHeight - 16;
      if (!placed.current || reducedMotion()) {
        gsap.set(el, { y, height, opacity: 1 });
        placed.current = true;
        return;
      }
      gsap.set(el, { height });
      gsap.to(el, { y, opacity: 1, duration: 0.28, ease: "power3.out", overwrite: true });
    },
    { dependencies: deps },
  );
}

function Navigation({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { can } = useAuth();
  const attention = useAttention();
  const location = useLocation();
  const list = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLSpanElement>(null);
  const items = ROUTES.filter((r) => r.label && can(...r.anyOf));
  useNavIndicator(list, bar, [location.pathname, collapsed, items.length]);
  return (
    <nav aria-label="Main" className="scroll-quiet flex-1 overflow-y-auto px-3 py-2">
      <div ref={list} className="relative">
      <span ref={bar} aria-hidden className="pointer-events-none absolute left-0 top-0 w-[3px] rounded-r-full bg-nav-indicator opacity-0" />
      {NAV_GROUPS.map((group) => {
        const entries = items.filter((r) => groupOf(r, can) === group.key);
        if (!entries.length) return null;
        return (
          <div key={group.key} className="mb-4">
            {collapsed ? (
              <div className="mx-auto mb-2 h-px w-6 bg-nav-line" aria-hidden />
            ) : (
              <div className="px-3 pb-1.5 text-xs font-medium text-nav-ink-muted">{group.label}</div>
            )}
            <ul className="space-y-0.5">
              {entries.map((item) => {
                const label = item.label!(can);
                const badge = typeof item.badge === "function" ? item.badge(can) : item.badge;
                const count = badge ? attention[badge] ?? 0 : 0;
                return (
                  <li key={item.path}>
                    <NavLink
                      to={item.path}
                      onClick={onNavigate}
                      title={collapsed ? (count ? `${label} (${count})` : label) : undefined}
                      className={({ isActive }) =>
                        cx(
                          "group relative flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors",
                          collapsed && "justify-center px-0",
                          isActive
                            ? "bg-nav-active text-white"
                            : "text-nav-ink hover:bg-nav-raised hover:text-white",
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          <span className={cx("shrink-0", isActive ? "text-nav-indicator" : "text-nav-ink-muted group-hover:text-nav-ink")}>
                            {item.icon}
                          </span>
                          <span className={cx("truncate", collapsed && "sr-only")}>{label}</span>
                          {count > 0 && (
                            <span
                              className={cx(
                                "tabular ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-nav-indicator px-1.5 text-[11px] font-semibold leading-5 text-nav",
                                collapsed && "absolute right-1 top-1 ml-0 min-w-4 px-1 text-[10px] leading-4",
                              )}
                            >
                              {count > 99 ? "99+" : count}
                              <span className="sr-only"> need attention</span>
                            </span>
                          )}
                        </>
                      )}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      </div>
    </nav>
  );
}

function AccountBlock({ collapsed }: { collapsed: boolean }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  const signOut = () =>
    // End on the landing page, so the next person to sign in is not sent to whatever page
    // this account had open.
    void logout().then(() => navigate("/", { replace: true }));
  return (
    <div className="border-t border-nav-line p-3">
      <div className={cx("flex items-center gap-3 rounded-lg px-2 py-2", collapsed && "justify-center px-0")}>
        <span className="[&>span]:bg-nav-raised [&>span]:text-nav-ink [&>span]:ring-nav-line">
          <Avatar name={user.name} size="sm" />
        </span>
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <PersonName
              name={user.name}
              verified={user.professionally_verified}
              source={user.verification_source}
              className="max-w-full text-sm font-semibold text-white"
            />
            <div className="truncate text-xs text-nav-ink-muted" title={ROLE_LABEL[user.role]}>
              {ROLE_LABEL[user.role]}
            </div>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={signOut}
        title={collapsed ? "Sign out" : undefined}
        className={cx(
          "mt-1 flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-sm font-medium text-nav-ink transition-colors hover:bg-nav-raised hover:text-white",
          collapsed && "justify-center px-0",
        )}
      >
        <LogOut className="h-4 w-4 shrink-0" aria-hidden />
        <span className={cx(collapsed && "sr-only")}>Sign out</span>
      </button>
    </div>
  );
}

function SidebarBrand({ collapsed }: { collapsed: boolean }) {
  return (
    <div className={cx("flex h-16 shrink-0 items-center gap-3 px-5", collapsed && "justify-center px-0")}>
      <BrandMark />
      {!collapsed && (
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[15px] font-semibold text-white">Next Best Action</div>
          <div className="truncate text-xs text-nav-ink-muted">Engagement intelligence</div>
        </div>
      )}
    </div>
  );
}

/** Account menu in the top bar: who is signed in, with which role, and sign-out. */
function AccountMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const hide = () => playExit([menu.current], () => setOpen(false), { y: -4 });
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node))
        playExit([menu.current], () => setOpen(false), { y: -4 });
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  if (!user) return null;
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account menu for ${user.name}`}
        onClick={() => (open ? hide() : setOpen(true))}
        className="flex min-h-10 items-center gap-2.5 rounded-lg py-1 pl-1 pr-2 transition-colors hover:bg-subtle"
      >
        <Avatar name={user.name} size="sm" />
        <span className="hidden min-w-0 text-left leading-tight md:block">
          <span className="block max-w-44 truncate text-sm font-semibold text-ink">{user.name}</span>
          <span className="block max-w-44 truncate text-xs text-ink-subtle">{ROLE_LABEL[user.role]}</span>
        </span>
        <ChevronDown className={cx("h-4 w-4 text-ink-subtle transition-transform duration-200", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div
          ref={menu}
          role="menu"
          className="animate-rise absolute right-0 mt-2 w-72 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-line bg-surface shadow-overlay"
          style={{ zIndex: "var(--z-drawer)" }}
        >
          <div className="border-b border-line px-4 py-3">
            <div className="text-xs text-ink-subtle">Signed in as</div>
            <PersonName
              name={user.name}
              verified={user.professionally_verified}
              source={user.verification_source}
              className="mt-0.5 max-w-full text-sm font-semibold text-ink"
            />
            <div className="truncate text-[13px] text-ink-muted" title={user.email}>
              {user.email}
            </div>
            <div className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-primary-soft px-2 py-0.5 text-xs font-semibold text-primary-ink">
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> {ROLE_LABEL[user.role]}
              {user.professionally_verified && " · Verified"}
            </div>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => void logout().then(() => navigate("/", { replace: true }))}
            className="flex w-full items-center gap-2.5 px-4 py-3 text-sm font-medium text-ink hover:bg-subtle"
          >
            <LogOut className="h-4 w-4 text-ink-subtle" aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}


function Shell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(preferences.navCollapsed);
  const [drawer, setDrawer] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const drawerPanel = useRef<HTMLElement>(null);
  const drawerBackdrop = useRef<HTMLDivElement>(null);
  const closeDrawer = () => {
    if (drawerBackdrop.current && !reducedMotion()) gsap.to(drawerBackdrop.current, { opacity: 0, duration: 0.18 });
    playExit([drawerPanel.current], () => setDrawer(false), { x: -32 });
  };

  // Close the mobile drawer and move focus to the page on every navigation.
  useEffect(() => {
    setDrawer(false);
    window.scrollTo({ top: 0 });
  }, [location.pathname]);

  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeDrawer();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [drawer]);

  const toggle = () =>
    setCollapsed((v) => {
      preferences.setNavCollapsed(!v);
      return !v;
    });

  if (!user) return null;
  return (
    <div className="min-h-dvh lg:flex">
      <a href="#main" className="skip-link">
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside
        aria-label="Application"
        className={cx(
          "sticky top-0 hidden h-dvh shrink-0 flex-col bg-nav lg:flex",
          collapsed ? "w-[76px]" : "w-64",
        )}
        style={{ zIndex: "var(--z-nav)" }}
      >
        <SidebarBrand collapsed={collapsed} />
        <Navigation collapsed={collapsed} />
        <div className={cx("px-3 pb-1", collapsed && "flex justify-center")}>
          <button
            type="button"
            onClick={toggle}
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            title={collapsed ? "Expand navigation" : "Collapse navigation"}
            className={cx(
              "flex min-h-9 items-center gap-3 rounded-lg px-3 text-xs font-medium text-nav-ink-muted transition-colors hover:bg-nav-raised hover:text-white",
              collapsed ? "justify-center px-0 w-10" : "w-full",
            )}
          >
            {collapsed ? <ChevronsRight className="h-4 w-4" aria-hidden /> : <ChevronsLeft className="h-4 w-4" aria-hidden />}
            {!collapsed && "Collapse"}
          </button>
        </div>
        <AccountBlock collapsed={collapsed} />
      </aside>

      {/* Mobile and tablet drawer */}
      {drawer && (
        <div className="lg:hidden" style={{ zIndex: "var(--z-drawer)", position: "relative" }}>
          <div ref={drawerBackdrop} className="animate-fade fixed inset-0 bg-black/45" aria-hidden onClick={closeDrawer} />
          <aside
            ref={drawerPanel}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="animate-drawer-left fixed inset-y-0 left-0 flex w-[min(20rem,86vw)] flex-col bg-nav shadow-overlay"
          >
            <div className="flex items-center justify-between pr-3">
              <SidebarBrand collapsed={false} />
              <button
                type="button"
                aria-label="Close navigation"
                autoFocus
                onClick={closeDrawer}
                className="grid h-10 w-10 place-items-center rounded-lg text-nav-ink hover:bg-nav-raised"
              >
                <X className="h-5 w-5" aria-hidden />
              </button>
            </div>
            <Navigation collapsed={false} onNavigate={() => setDrawer(false)} />
            <AccountBlock collapsed={false} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="sticky top-0 flex h-16 items-center gap-3 border-b border-line bg-canvas/85 px-4 backdrop-blur-md sm:px-6 lg:px-8"
          style={{ zIndex: "var(--z-sticky)" }}
        >
          <IconButton label="Open navigation" className="-ml-2 lg:hidden" onClick={() => setDrawer(true)}>
            <Menu className="h-5 w-5" aria-hidden />
          </IconButton>
          <div className="flex min-w-0 items-center gap-2.5 lg:hidden">
            <BrandMark small />
            <span className="hidden truncate text-[15px] font-semibold text-ink sm:inline">Next Best Action</span>
          </div>
          <p className="hidden min-w-0 truncate text-[13px] text-ink-subtle xl:block">
            Access follows your role and assignments. All records are synthetic.
          </p>
          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            <ThemeToggle />
            <AccountMenu />
          </div>
        </header>
        <main
          id="main"
          ref={mainRef}
          tabIndex={-1}
          className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 outline-none sm:px-6 sm:py-8 lg:px-8"
        >
          <div key={location.pathname} className="animate-rise">
            <AppErrorBoundary resetKey={location.pathname}>
              <Suspense fallback={<Loading />}>{children}</Suspense>
            </AppErrorBoundary>
          </div>
        </main>
        <footer className="mx-auto w-full max-w-[1440px] border-t border-line px-4 py-4 sm:px-6 lg:px-8">
          <LegalLinks />
        </footer>
      </div>
    </div>
  );
}

/** Renders the page only if the signed-in account holds one of the permissions. */
function Guard({ anyOf, children }: { anyOf: Permission[]; children: ReactNode }) {
  const { can } = useAuth();
  return can(...anyOf) ? <>{children}</> : <AccessDenied />;
}

function Restoring() {
  return (
    <div role="status" className="grid min-h-dvh place-items-center bg-canvas">
      <div className="flex flex-col items-center gap-4 text-sm text-ink-subtle">
        <BrandMark />
        Restoring your session
      </div>
    </div>
  );
}

export default function App() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Restoring />;

  // An invitation link opens on its own page whether or not someone is signed in, so a
  // person signed in as another account is told so instead of being redirected away.
  // The legal documents are readable by anyone, signed in or not, and while acceptance is pending.
  if (matchPath("/legal/:kind", location.pathname)) {
    return (
      <Routes>
        <Route path="/legal/:kind" element={<LegalPage />} />
      </Routes>
    );
  }

  if (matchPath("/invite/:token", location.pathname)) {
    return (
      <Routes>
        <Route path="/invite/:token" element={<Invite />} />
      </Routes>
    );
  }

  if (!user) {
    // Signed out: only the public pages exist. Anything else goes to sign-in, and the
    // address asked for is remembered so sign-in can return there if the role allows it.
    return (
      <Routes>
        <Route
          path="/"
          element={
            <Suspense fallback={<div className="min-h-dvh bg-canvas" />}>
              <Landing />
            </Suspense>
          }
        />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/signup/verify" element={<VerifyOtp />} />
        <Route
          path="*"
          element={
            isAppPath(location.pathname) ? (
              <Navigate to="/login" replace state={{ from: location.pathname, reason: "signin_required" }} />
            ) : (
              <ErrorScreen kind="not_found" />
            )
          }
        />
      </Routes>
    );
  }

  // Updated Terms / Privacy Policy, or a withdrawn health-information consent: the server
  // refuses everything else until they are accepted, and this screen explains why.
  if (user.pending_consents?.length) {
    // Data & privacy stays reachable (the server allows it): requests, export, history.
    if (location.pathname === "/privacy") {
      return (
        <div className="min-h-dvh bg-canvas">
          <div className="mx-auto max-w-[1200px] px-4 py-6 sm:px-6 sm:py-8">
            <NavLink to="/" className="mb-4 inline-flex min-h-10 items-center text-sm font-semibold text-primary-ink">
              ← Back to review
            </NavLink>
            <Suspense fallback={<Loading />}>
              <RestrictedPrivacy />
            </Suspense>
          </div>
        </div>
      );
    }
    return <Reaccept />;
  }

  return (
    <Shell>
      <Routes>
        {/* Signed in: the public pages lead straight to the account's own dashboard. */}
        {PUBLIC_PATHS.map((path) => (
          <Route key={path} path={path} element={<Navigate to={user.home} replace />} />
        ))}
        {ROUTES.map((r) => (
          <Route key={r.path} path={r.path} element={<Guard anyOf={r.anyOf}>{r.element}</Guard>} />
        ))}
        <Route path="/denied" element={<AccessDenied />} />
        <Route path="*" element={<AccessDenied missing />} />
      </Routes>
    </Shell>
  );
}
