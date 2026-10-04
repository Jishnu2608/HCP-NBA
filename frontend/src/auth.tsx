import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api, post, setAcceptanceRequiredHandler, setSessionLostHandler } from "./api";
import type { Permission } from "./permissions";
import { pendingSignup } from "./session";
import type { Challenge } from "./session";

export type Role = "admin" | "compliance" | "medical_rep" | "care_manager" | "hcp" | "patient";

/** The signed-in account, exactly as the server describes it. */
export interface User {
  id: number;
  name: string;
  email: string;
  role: Role;
  permissions: Permission[];
  home: string;
  hcp_id: string | null;
  patient_id: string | null;
  email_verified: boolean;
  /** Set by the server only after an authorised invitation (or for platform-provisioned staff). */
  professionally_verified: boolean;
  verification_source: "invitation" | "system" | null;
  /** What this account must accept before the server serves anything else. */
  pending_consents: string[];
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  compliance: "Compliance / MLR Reviewer",
  medical_rep: "Medical Representative",
  care_manager: "Care Manager",
  hcp: "Healthcare Professional",
  patient: "Patient",
};

/** Patient self-registration. There is no role field: professionals join by invitation. */
export interface SignupForm {
  name: string;
  email: string;
  date_of_birth: string;
  password: string;
  confirm_password: string;
  country: string;
  region: string | null;
  accept_terms: boolean;
  consent_health_data: boolean;
}

export interface AcceptForm {
  token: string;
  name: string;
  date_of_birth: string;
  password: string;
  confirm_password: string;
  country: string;
  region: string | null;
  accept_terms: boolean;
}

interface AuthState {
  user: User | null;
  loading: boolean;
  can: (...anyOf: Permission[]) => boolean;
  /** Resolves to the account, or to a pending challenge if the email is not verified yet. */
  login: (email: string, password: string) => Promise<User | Challenge>;
  signup: (form: SignupForm) => Promise<Challenge>;
  acceptInvitation: (form: AcceptForm) => Promise<Challenge>;
  verify: (code: string) => Promise<User>;
  resend: () => Promise<Challenge>;
  logout: () => Promise<void>;
  /** Ask the server again who is signed in (after accepting updated documents). */
  refresh: () => Promise<void>;
  /** Use the account the server just signed in (after a demo reset). */
  adopt: (result: { user: User }) => User;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const client = useQueryClient();

  const drop = useCallback(() => {
    client.clear();
    setUser(null);
  }, [client]);

  useEffect(() => {
    setSessionLostHandler(drop);
    // Re-read the account so the re-acceptance screen appears.
    setAcceptanceRequiredHandler(() => {
      api<User>("/auth/me").then(setUser).catch(() => undefined);
    });
    // The session cookie is invisible to this code, so ask the server who is signed in.
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [drop]);

  const adopt = useCallback(
    (result: { user: User }) => {
      pendingSignup.clear();
      client.clear(); // never show one account data fetched as another
      setUser(result.user);
      return result.user;
    },
    [client],
  );

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      can: (...anyOf) => anyOf.some((p) => user?.permissions.includes(p) ?? false),
      adopt,
      login: async (email, password) => {
        try {
          return adopt(await post("/auth/login", { email, password }));
        } catch (error) {
          if (error instanceof ApiError && error.code === "verification_required") {
            return pendingSignup.save(error.detail);
          }
          throw error;
        }
      },
      signup: async (form) => pendingSignup.save(await post("/auth/signup", form)),
      acceptInvitation: async (form) => pendingSignup.save(await post("/invitations/accept", form)),
      verify: async (code) => adopt(await post("/auth/verify-otp", { code })),
      resend: async () => pendingSignup.save(await post("/auth/resend-otp")),
      refresh: async () => {
        setUser(await api<User>("/auth/me"));
      },
      logout: async () => {
        // Ends the session on the server, so the cookie is dead even if copied.
        await post("/auth/logout").catch(() => undefined);
        drop();
      },
    }),
    [user, loading, adopt, drop],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export const isChallenge = (value: User | Challenge): value is Challenge => "expiresAt" in value;
