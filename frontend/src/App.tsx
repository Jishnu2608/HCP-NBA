import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  CalendarDays,
  ClipboardList,
  Cpu,
  FileCheck2,
  Inbox,
  LogOut,
  Pill,
  ScrollText,
  Settings2,
  ShieldCheck,
  Stethoscope,
  UserRound,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { ROLE_LABEL, useAuth } from "./auth";
import type { Role } from "./auth";
import Admin from "./pages/Admin";
import Audit from "./pages/Audit";
import ContentLibrary from "./pages/Content";
import Dashboard from "./pages/Dashboard";
import { HcpList, HcpProfile } from "./pages/Hcps";
import Login from "./pages/Login";
import NbaDetail from "./pages/NbaDetail";
import { PatientList, PatientProfile } from "./pages/Patients";
import { MyConsents, MyInbox, MyMedications, MyPatients, MyProfile } from "./pages/Portal";
import Queue from "./pages/Queue";
import UnderTheHood from "./pages/UnderTheHood";
import { Loading, cx, fmtDate } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  element: ReactNode;
}

const icon = (Icon: typeof Activity) => <Icon className="h-4 w-4" />;

// Navigation is role-driven, and mirrors what the API will actually allow.
const NAV: Record<Role, NavItem[]> = {
  care_manager: [
    { to: "/queue", label: "Adherence queue", icon: icon(ClipboardList), element: <Queue /> },
    { to: "/patients", label: "My patients", icon: icon(Users), element: <PatientList /> },
    { to: "/content", label: "Approved content", icon: icon(FileCheck2), element: <ContentLibrary /> },
  ],
  medical_rep: [
    { to: "/queue", label: "HCP queue", icon: icon(ClipboardList), element: <Queue /> },
    { to: "/hcps", label: "My HCPs", icon: icon(Stethoscope), element: <HcpList /> },
    { to: "/content", label: "Approved content", icon: icon(FileCheck2), element: <ContentLibrary /> },
  ],
  compliance: [
    { to: "/content", label: "Content & MLR", icon: icon(FileCheck2), element: <ContentLibrary /> },
    { to: "/queue", label: "Gate outcomes", icon: icon(ShieldCheck), element: <Queue /> },
    { to: "/audit", label: "Audit log", icon: icon(ScrollText), element: <Audit /> },
    { to: "/dashboard", label: "Metrics", icon: icon(BarChart3), element: <Dashboard /> },
    { to: "/under-the-hood", label: "Under the hood", icon: icon(Cpu), element: <UnderTheHood /> },
  ],
  hcp: [
    { to: "/inbox", label: "Inbox", icon: icon(Inbox), element: <MyInbox /> },
    { to: "/my-patients", label: "My patients", icon: icon(Users), element: <MyPatients /> },
    { to: "/profile", label: "Profile", icon: icon(UserRound), element: <MyProfile /> },
  ],
  patient: [
    { to: "/medications", label: "My medications", icon: icon(Pill), element: <MyMedications /> },
    { to: "/inbox", label: "Messages", icon: icon(Inbox), element: <MyInbox /> },
    { to: "/consent", label: "Consent & preferences", icon: icon(ShieldCheck), element: <MyConsents /> },
  ],
  admin: [
    { to: "/dashboard", label: "Dashboard", icon: icon(BarChart3), element: <Dashboard /> },
    { to: "/queue", label: "All recommendations", icon: icon(ClipboardList), element: <Queue /> },
    { to: "/patients", label: "Patients", icon: icon(Users), element: <PatientList /> },
    { to: "/hcps", label: "HCPs", icon: icon(Stethoscope), element: <HcpList /> },
    { to: "/content", label: "Content", icon: icon(FileCheck2), element: <ContentLibrary /> },
    { to: "/audit", label: "Audit log", icon: icon(ScrollText), element: <Audit /> },
    { to: "/admin", label: "Engine", icon: icon(Settings2), element: <Admin /> },
    { to: "/under-the-hood", label: "Under the hood", icon: icon(Cpu), element: <UnderTheHood /> },
  ],
};

const DETAIL_ROUTES: Partial<Record<Role, Array<{ path: string; element: ReactNode }>>> = {
  care_manager: [
    { path: "/nba/:id", element: <NbaDetail /> },
    { path: "/patients/:id", element: <PatientProfile /> },
  ],
  medical_rep: [
    { path: "/nba/:id", element: <NbaDetail /> },
    { path: "/hcps/:id", element: <HcpProfile /> },
  ],
  compliance: [{ path: "/nba/:id", element: <NbaDetail /> }],
  admin: [
    { path: "/nba/:id", element: <NbaDetail /> },
    { path: "/patients/:id", element: <PatientProfile /> },
    { path: "/hcps/:id", element: <HcpProfile /> },
  ],
};

function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const clock = useQuery({ queryKey: ["clock"], queryFn: () => api("/clock") });
  if (!user) return null;
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
        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {NAV[user.role].map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium",
                  isActive ? "bg-brand-50 text-brand-700" : "text-stone-600 hover:bg-stone-100",
                )
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-stone-200 p-3">
          <div className="px-2 pb-2">
            <div className="truncate text-sm font-medium text-stone-900">{user.display_name}</div>
            <div className="text-xs text-stone-500">{ROLE_LABEL[user.role]}</div>
          </div>
          <button
            onClick={logout}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-stone-600 hover:bg-stone-100"
          >
            <LogOut className="h-4 w-4" /> Switch persona
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-stone-200 bg-white px-6 py-2.5">
          <div className="text-xs text-stone-500">
            Synthetic demonstration data. No real patient or HCP information.
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

export default function App() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Signing in" />;
  if (!user) {
    return location.pathname === "/login" ? <Login /> : <Navigate to="/login" replace />;
  }
  const items = NAV[user.role];
  return (
    <Shell>
      <Routes>
        {items.map((item) => (
          <Route key={item.to} path={item.to} element={item.element} />
        ))}
        {(DETAIL_ROUTES[user.role] ?? []).map((r) => (
          <Route key={r.path} path={r.path} element={r.element} />
        ))}
        <Route path="*" element={<Navigate to={items[0].to} replace />} />
      </Routes>
    </Shell>
  );
}
