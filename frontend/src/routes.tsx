// One table of application routes. Each route names the permissions that allow it; the
// navigation menu and the route guards are both derived from this table, so a page cannot
// be linked without being guarded, or guarded differently from how it is linked.
import {
  BarChart3,
  ClipboardList,
  Cpu,
  FileCheck2,
  Inbox,
  Pill,
  ScrollText,
  Settings2,
  ShieldCheck,
  Stethoscope,
  UserCog,
  UserRound,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { matchPath } from "react-router-dom";
import Admin from "./pages/Admin";
import Audit from "./pages/Audit";
import ContentLibrary from "./pages/Content";
import Dashboard from "./pages/Dashboard";
import { HcpList, HcpProfile } from "./pages/Hcps";
import NbaDetail from "./pages/NbaDetail";
import { PatientList, PatientProfile } from "./pages/Patients";
import { MyConsents, MyInbox, MyMedications, MyPatients, MyProfile } from "./pages/Portal";
import Queue from "./pages/Queue";
import UnderTheHood from "./pages/UnderTheHood";
import UsersPage from "./pages/Users";
import { CONTENT_READ, HCP_READ, NBA_READ, P, PATIENT_READ } from "./permissions";
import type { Permission } from "./permissions";

type Can = (...anyOf: Permission[]) => boolean;

export interface AppRoute {
  path: string;
  /** The account needs at least one of these. */
  anyOf: Permission[];
  element: ReactNode;
  /** Present for routes that appear in the navigation menu. */
  label?: (can: Can) => string;
  icon?: ReactNode;
}

const icon = (Icon: typeof Users) => <Icon className="h-4 w-4" />;

/** True if an account holding `permissions` may open `path`. Used to decide where to land
 *  after sign-in; the route guard and the API still make the real decision. */
export function mayOpen(path: string | undefined, permissions: Permission[]): boolean {
  if (!path) return false;
  const route = ROUTES.find((r) => matchPath(r.path, path));
  return Boolean(route && route.anyOf.some((p) => permissions.includes(p)));
}

export const ROUTES: AppRoute[] = [
  {
    path: "/dashboard",
    anyOf: [P.ANALYTICS_READ],
    element: <Dashboard />,
    label: () => "Dashboard",
    icon: icon(BarChart3),
  },
  {
    path: "/queue",
    anyOf: NBA_READ,
    element: <Queue />,
    label: (can) =>
      can(P.NBA_READ_ALL)
        ? "All recommendations"
        : can(P.NBA_READ_GATED)
          ? "Gate outcomes"
          : can(P.NBA_READ_HCP_ASSIGNED)
            ? "HCP queue"
            : "Adherence queue",
    icon: icon(ClipboardList),
  },
  {
    path: "/patients",
    anyOf: PATIENT_READ,
    element: <PatientList />,
    label: (can) => (can(P.PATIENT_READ_ALL) ? "Patients" : "My patients"),
    icon: icon(Users),
  },
  {
    path: "/hcps",
    anyOf: HCP_READ,
    element: <HcpList />,
    label: (can) => (can(P.HCP_READ_ALL) ? "HCPs" : "My HCPs"),
    icon: icon(Stethoscope),
  },
  {
    path: "/content",
    anyOf: CONTENT_READ,
    element: <ContentLibrary />,
    label: (can) =>
      can(P.CONTENT_APPROVE) ? "Content & MLR" : can(P.CONTENT_READ_ALL) ? "Content" : "Approved content",
    icon: icon(FileCheck2),
  },
  {
    path: "/audit",
    anyOf: [P.AUDIT_READ],
    element: <Audit />,
    label: () => "Audit log",
    icon: icon(ScrollText),
  },
  {
    path: "/users",
    anyOf: [P.USER_MANAGE],
    element: <UsersPage />,
    label: () => "Users & assignments",
    icon: icon(UserCog),
  },
  {
    path: "/admin",
    anyOf: [P.ENGINE_OPERATE],
    element: <Admin />,
    label: () => "Engine",
    icon: icon(Settings2),
  },
  {
    path: "/under-the-hood",
    anyOf: [P.MODELS_READ],
    element: <UnderTheHood />,
    label: () => "Under the hood",
    icon: icon(Cpu),
  },
  {
    path: "/medications",
    anyOf: [P.SELF_CONSENT_MANAGE],
    element: <MyMedications />,
    label: () => "My medications",
    icon: icon(Pill),
  },
  {
    path: "/inbox",
    anyOf: [P.SELF_INBOX],
    element: <MyInbox />,
    label: (can) => (can(P.SELF_CONSENT_MANAGE) ? "Messages" : "Inbox"),
    icon: icon(Inbox),
  },
  {
    path: "/consent",
    anyOf: [P.SELF_CONSENT_MANAGE],
    element: <MyConsents />,
    label: () => "Consent & preferences",
    icon: icon(ShieldCheck),
  },
  {
    path: "/my-patients",
    anyOf: [P.SELF_PATIENTS_READ],
    element: <MyPatients />,
    label: () => "My patients",
    icon: icon(Users),
  },
  {
    path: "/profile",
    anyOf: [P.SELF_PATIENTS_READ],
    element: <MyProfile />,
    label: () => "Profile",
    icon: icon(UserRound),
  },
  // Detail pages: guarded the same way, not shown in the menu.
  { path: "/nba/:id", anyOf: NBA_READ, element: <NbaDetail /> },
  { path: "/patients/:id", anyOf: PATIENT_READ, element: <PatientProfile /> },
  { path: "/hcps/:id", anyOf: HCP_READ, element: <HcpProfile /> },
];
