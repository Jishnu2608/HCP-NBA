import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken, post, setToken } from "./api";

export type Role = "admin" | "compliance" | "medical_rep" | "care_manager" | "hcp" | "patient";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  hcp_id: string | null;
  patient_id: string | null;
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  compliance: "Compliance / MLR",
  medical_rep: "Medical Representative",
  care_manager: "Care Manager",
  hcp: "Healthcare Professional",
  patient: "Patient",
};

interface AuthState {
  user: User | null;
  loading: boolean;
  loginAs: (username: string) => Promise<User>;
  login: (username: string, password: string) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));
  const client = useQueryClient();

  useEffect(() => {
    if (!getToken()) return;
    api<User>("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  const accept = useCallback(
    (result: { access_token: string; user: User }) => {
      setToken(result.access_token);
      client.clear(); // never show one persona data fetched as another
      setUser(result.user);
      return result.user;
    },
    [client],
  );

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      loginAs: async (username) => accept(await post("/auth/demo-login", { username })),
      login: async (username, password) =>
        accept(await api("/auth/login", { method: "POST", form: { username, password } })),
      logout: () => {
        setToken(null);
        client.clear();
        setUser(null);
      },
    }),
    [user, loading, accept, client],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
