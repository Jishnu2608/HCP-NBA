import { useQuery } from "@tanstack/react-query";
import { Activity, CalendarDays, LogOut } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { ROLE_LABEL, useAuth } from "./auth";
import AccessDenied from "./pages/AccessDenied";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import VerifyOtp from "./pages/VerifyOtp";
import type { Permission } from "./permissions";
import { ROUTES } from "./routes";
import { Loading, cx, fmtDate } from "./ui";

const PUBLIC_PATHS = ["/", "/login", "/signup", "/signup/verify"];

function Shell({ children }: { children: ReactNode }) {
  const { user, can, logout } = useAuth();
  const navigate = useNavigate();
  const clock = useQuery({ queryKey: ["clock"], queryFn: () => api("/clock") });
  if (!user) return null;
  const nav = ROUTES.filter((r) => r.label && can(...r.anyOf));
  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-stone-200 bg-white">
        <div className="flex items-center gap-2 px-5 py-4">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-white">
            <Activity className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-tight text-stone-900">Next Best Action</div>
            <div className="text-[11px] leading-tight text-stone-500">Healthcare engagement</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2">
          {nav.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium",
                  isActive ? "bg-brand-50 text-brand-700" : "text-stone-600 hover:bg-stone-100",
                )
              }
            >
              {item.icon}
              {item.label!(can)}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-stone-200 p-3">
          <div className="rounded-lg bg-stone-50 px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-stone-400">
              Signed in as
            </div>
            <div className="mt-0.5 truncate text-sm font-semibold text-stone-900">{user.name}</div>
            <div className="text-xs font-medium text-brand-700">{ROLE_LABEL[user.role]}</div>
            <div className="mt-0.5 truncate text-xs text-stone-500">{user.email}</div>
          </div>
          <button
            onClick={() =>
              // End on the landing page, so the next person to sign in is not sent to
              // whatever page this account had open.
              void logout().then(() => navigate("/", { replace: true }))
            }
            className="mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-sm text-stone-600 hover:bg-stone-100"
          >
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-stone-200 bg-white px-6 py-2.5">
          <div className="text-xs text-stone-500">
            You see what your role and assignments allow. Synthetic demonstration data only.
          </div>
          <div className="flex items-center gap-1.5 rounded-full bg-stone-100 px-3 py-1 text-xs font-medium text-stone-700">
            <CalendarDays className="h-3.5 w-3.5" />
            Demo date {fmtDate(clock.data?.as_of_date)}
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</main>
      </div>
    </div>
  );
}

/** Renders the page only if the signed-in account holds one of the permissions. */
function Guard({ anyOf, children }: { anyOf: Permission[]; children: ReactNode }) {
  const { can } = useAuth();
  return can(...anyOf) ? <>{children}</> : <AccessDenied />;
}

export default function App() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Restoring your session" />;

  if (!user) {
    // Signed out: only the public pages exist. Anything else goes to sign-in, and the
    // address asked for is remembered so sign-in can return there if the role allows it.
    return (
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/signup/verify" element={<VerifyOtp />} />
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      </Routes>
    );
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
