import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api, post, setSessionLostHandler } from "./api";
import type { Permission } from "./permissions";
import { pendingSignup, session } from "./session";
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
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  compliance: "Compliance / MLR Reviewer",
  medical_rep: "Medical Representative",
  care_manager: "Care Manager",
  hcp: "Healthcare Professional",
  patient: "Patient",
};

export interface SignupForm {
  name: string;
  email: string;
  password: string;
  confirm_password: string;
  role: string;
}

interface AuthState {
  user: User | null;
  loading: boolean;
  can: (...anyOf: Permission[]) => boolean;
  /** Resolves to the account, or to a pending challenge if the email is not verified yet. */
  login: (email: string, password: string) => Promise<User | Challenge>;
  signup: (form: SignupForm) => Promise<Challenge>;
  verify: (code: string) => Promise<User>;
  resend: () => Promise<Challenge>;
  logout: () => Promise<void>;
  /** Replace the session with one the server just issued (after a demo reset). */
  adopt: (result: { access_token: string; user: User }) => User;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(session.token()));
  const client = useQueryClient();

  const drop = useCallback(() => {
    session.end();
    client.clear();
    setUser(null);
  }, [client]);

  useEffect(() => {
    setSessionLostHandler(drop);
    if (!session.token()) return;
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => session.end())
      .finally(() => setLoading(false));
  }, [drop]);

  const adopt = useCallback(
    (result: { access_token: string; user: User }) => {
      session.start(result.access_token);
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
          return adopt(
            await api("/auth/login", { method: "POST", form: { username: email, password } }),
          );
        } catch (error) {
          if (error instanceof ApiError && error.code === "verification_required") {
            return pendingSignup.save(error.detail);
          }
          throw error;
        }
      },
      signup: async (form) => pendingSignup.save(await post("/auth/signup", form)),
      verify: async (code) => {
        const pending = pendingSignup.get();
        if (!pending) throw new Error("Nothing to verify. Sign up or sign in first.");
        return adopt(
          await post("/auth/verify-otp", {
            verification_token: pending.verification_token,
            code,
          }),
        );
      },
      resend: async () => {
        const pending = pendingSignup.get();
        if (!pending) throw new Error("Nothing to verify. Sign up or sign in first.");
        return pendingSignup.save(
          await post("/auth/resend-otp", { verification_token: pending.verification_token }),
        );
      },
      logout: async () => {
        // Ends the session on the server first, so the token is dead even if copied.
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

export const isChallenge = (value: User | Challenge): value is Challenge =>
  "verification_token" in value;
