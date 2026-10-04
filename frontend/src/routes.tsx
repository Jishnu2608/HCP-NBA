// One table of application routes. Each route names the permissions that allow it; the
// navigation menu and the route guards are both derived from this table, so a page cannot
// be linked without being guarded, or guarded differently from how it is linked.
import {
  BarChart3,
  ClipboardList,
  Cpu,
  FileCheck2,
  FileLock2,
  Inbox,
  Lock,
  MailPlus,
  Pill,
  ScrollText,
  Settings2,
  ShieldCheck,
  Stethoscope,
  UserCog,
  UserRound,
  Users,
} from "lucide-react";
import { lazy } from "react";
import type { ComponentType, ReactNode } from "react";
import { matchPath } from "react-router-dom";

// Pages load on first visit, so a role downloads only the screens it opens (the charts
// library, for example, is fetched only with the dashboard). If a page's file is gone
// because a new version was deployed while this tab was open, reload once to pick up the
// new version; if this load already was a reload, let the error surface instead of looping.
function page<T extends { default: ComponentType }>(load: () => Promise<T>) {
  return lazy(() =>
    load().catch((error) => {
      const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
      if (nav?.type !== "reload") window.location.reload();
      throw error;
    }),
  );
}
const Admin = page(() => import("./pages/Admin"));
const Audit = page(() => import("./pages/Audit"));
const ContentLibrary = page(() => import("./pages/Content"));
const Dashboard = page(() => import("./pages/Dashboard"));
const HcpList = page(() => import("./pages/Hcps").then((m) => ({ default: m.HcpList })));
const HcpProfile = page(() => import("./pages/Hcps").then((m) => ({ default: m.HcpProfile })));
const NbaDetail = page(() => import("./pages/NbaDetail"));
const PatientList = page(() => import("./pages/Patients").then((m) => ({ default: m.PatientList })));
const PatientProfile = page(() => import("./pages/Patients").then((m) => ({ default: m.PatientProfile })));
const MyConsents = page(() => import("./pages/Portal").then((m) => ({ default: m.MyConsents })));
const MyInbox = page(() => import("./pages/Portal").then((m) => ({ default: m.MyInbox })));
const MyMedications = page(() => import("./pages/Portal").then((m) => ({ default: m.MyMedications })));
const MyPatients = page(() => import("./pages/Portal").then((m) => ({ default: m.MyPatients })));
const MyProfile = page(() => import("./pages/Portal").then((m) => ({ default: m.MyProfile })));
const Queue = page(() => import("./pages/Queue"));
const UnderTheHood = page(() => import("./pages/UnderTheHood"));
const UsersPage = page(() => import("./pages/Users"));
const InvitationsPage = page(() => import("./pages/Invitations"));
const PrivacyPage = page(() => import("./pages/Privacy"));
const PrivacyRequestsPage = page(() => import("./pages/PrivacyRequests"));
import { CONTENT_READ, HCP_READ, INVITE_ANY, NBA_READ, P, PATIENT_READ } from "./permissions";
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
  /** Menu section the item is listed under (a function when it depends on the account). */
  group?: NavGroup | ((can: Can) => NavGroup);
}

export const groupOf = (route: AppRoute, can: Can): NavGroup | undefined =>
  typeof route.group === "function" ? route.group(can) : route.group;

export type NavGroup = "work" | "self" | "governance" | "admin";
export const NAV_GROUPS: Array<{ key: NavGroup; label: string }> = [
  { key: "work", label: "Workspace" },
  { key: "self", label: "My account" },
  { key: "governance", label: "Governance" },
  { key: "admin", label: "Administration" },
];

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
    group: "work",
  },
  {
    path: "/queue",
    anyOf: NBA_READ,
    element: <Queue />,
    label: (can) =>
      can(P.NBA_READ_ALL)
        ? "All recommendations"
        : can(P.NBA_READ_GATED)
          ? "Blocked & held back"
          : can(P.NBA_READ_HCP_ASSIGNED)
            ? "HCP queue"
            : "Adherence queue",
    icon: icon(ClipboardList),
    group: "work",
  },
  {
    path: "/patients",
    anyOf: PATIENT_READ,
    element: <PatientList />,
    label: (can) => (can(P.PATIENT_READ_ALL) ? "Patients" : "My patients"),
    icon: icon(Users),
    group: "work",
  },
  {
    path: "/hcps",
    anyOf: HCP_READ,
    element: <HcpList />,
    label: (can) => (can(P.HCP_READ_ALL) ? "HCPs" : "My HCPs"),
    icon: icon(Stethoscope),
    group: "work",
  },
  {
    path: "/content",
    anyOf: CONTENT_READ,
    element: <ContentLibrary />,
    label: (can) =>
      can(P.CONTENT_APPROVE) ? "Content & MLR" : can(P.CONTENT_READ_ALL) ? "Content" : "Approved content",
    icon: icon(FileCheck2),
    group: "governance",
  },
  {
    path: "/audit",
    anyOf: [P.AUDIT_READ],
    element: <Audit />,
    label: () => "Audit log",
    icon: icon(ScrollText),
    group: "governance",
  },
  {
    path: "/users",
    anyOf: [P.USER_MANAGE],
    element: <UsersPage />,
    label: () => "Users & assignments",
    icon: icon(UserCog),
    group: "admin",
  },
  {
    path: "/admin",
    anyOf: [P.ENGINE_OPERATE],
    element: <Admin />,
    label: () => "Engine",
    icon: icon(Settings2),
    group: "admin",
  },
  {
    path: "/under-the-hood",
    anyOf: [P.MODELS_READ],
    element: <UnderTheHood />,
    label: () => "Under the hood",
    icon: icon(Cpu),
    group: "admin",
  },
  {
    path: "/medications",
    anyOf: [P.SELF_CONSENT_MANAGE],
    element: <MyMedications />,
    label: () => "My medications",
    icon: icon(Pill),
    group: "self",
  },
  {
    path: "/inbox",
    anyOf: [P.SELF_INBOX],
    element: <MyInbox />,
    label: (can) => (can(P.SELF_CONSENT_MANAGE) ? "Messages" : "Inbox"),
    icon: icon(Inbox),
    group: "self",
  },
  {
    path: "/consent",
    anyOf: [P.SELF_CONSENT_MANAGE],
    element: <MyConsents />,
    label: () => "Consent & preferences",
    icon: icon(ShieldCheck),
    group: "self",
  },
  {
    path: "/my-patients",
    anyOf: [P.SELF_PATIENTS_READ],
    element: <MyPatients />,
    label: () => "My patients",
    icon: icon(Users),
    group: "self",
  },
  {
    path: "/profile",
    anyOf: [P.SELF_PATIENTS_READ],
    element: <MyProfile />,
    label: () => "Profile",
    icon: icon(UserRound),
    group: "self",
  },
  {
    // Admin: every invitation, under Administration. HCP: the ones they sent, as "Team".
    path: "/invitations",
    anyOf: INVITE_ANY,
    element: <InvitationsPage />,
    label: (can) => (can(P.INVITATION_READ_ALL) ? "Invitations" : "Team"),
    icon: icon(MailPlus),
    group: (can) => (can(P.INVITATION_READ_ALL) ? "admin" : "self"),
  },
  {
    path: "/privacy",
    anyOf: [P.PRIVACY_SELF],
    element: <PrivacyPage />,
    label: () => "Data & privacy",
    icon: icon(Lock),
    group: "self",
  },
  {
    path: "/privacy-requests",
    anyOf: [P.PRIVACY_MANAGE],
    element: <PrivacyRequestsPage />,
    label: () => "Privacy requests",
    icon: icon(FileLock2),
    group: "admin",
  },
  // Detail pages: guarded the same way, not shown in the menu.
  { path: "/nba/:id", anyOf: NBA_READ, element: <NbaDetail /> },
  { path: "/patients/:id", anyOf: PATIENT_READ, element: <PatientProfile /> },
  { path: "/hcps/:id", anyOf: HCP_READ, element: <HcpProfile /> },
];
