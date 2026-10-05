// The only module that touches browser storage. (The one exception is the theme bootstrap
// script in index.html, which must run before the app loads to avoid a flash of the wrong
// theme; it reads the same THEME_KEY.)
//
// Nothing here is a credential. The session and the verification step live in HttpOnly
// cookies set by the server, which JavaScript cannot read. This module keeps only what the
// screens need to display: the pending sign-up's email and timers, and display preferences.

const CHALLENGE_KEY = "nba.signup.challenge";
const THEME_KEY = "nba.theme";
const NAV_KEY = "nba.nav.collapsed";
// "Skip for now" on the health-profile prompt, per account (a display preference only).
const PROFILE_SKIP_KEY = "nba.profile.skipped";
// Sessions from before the move to cookies; removed on load.
const LEGACY_SESSION_KEY = "nba.session.token";

const store = () => window.sessionStorage;
try {
  store().removeItem(LEGACY_SESSION_KEY);
} catch {
  /* storage unavailable */
}

/** A sign-up waiting for its one-time code: display data only, no token, no password. */
export interface Challenge {
  email: string;
  /** "failed": the email could not be sent; no code exists until a resend succeeds. */
  delivery: "email" | "development" | "failed";
  expiresAt: number;
  resendAt: number;
  dev_otp?: string;
}

interface ChallengePayload {
  email: string;
  delivery?: "email" | "development" | "failed";
  expires_in?: number;
  resend_in?: number;
  dev_otp?: string;
}

export const pendingSignup = {
  get: (): Challenge | null => {
    try {
      const raw = store().getItem(CHALLENGE_KEY);
      return raw ? (JSON.parse(raw) as Challenge) : null;
    } catch {
      return null;
    }
  },
  /** Stores what the API returned; fields missing from a partial update keep their value. */
  save: (payload: ChallengePayload): Challenge => {
    const previous = pendingSignup.get();
    const now = Date.now();
    const challenge: Challenge = {
      email: payload.email,
      delivery: payload.delivery ?? previous?.delivery ?? "email",
      expiresAt: now + (payload.expires_in ?? 0) * 1000,
      resendAt: now + (payload.resend_in ?? 0) * 1000,
      dev_otp: payload.delivery ? payload.dev_otp : previous?.dev_otp,
    };
    store().setItem(CHALLENGE_KEY, JSON.stringify(challenge));
    return challenge;
  },
  clear: () => store().removeItem(CHALLENGE_KEY),
};

// Storage can be unavailable (private windows, blocked site data); preferences then last
// for the visit only.
function readLocal(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeLocal(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* not persisted */
  }
}

export type ThemeChoice = "light" | "dark";

export const preferences = {
  /** The theme the user picked explicitly, or null to follow the operating system. */
  theme: (): ThemeChoice | null => {
    const value = readLocal(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  },
  setTheme: (theme: ThemeChoice) => writeLocal(THEME_KEY, theme),
  navCollapsed: () => readLocal(NAV_KEY) === "1",
  setNavCollapsed: (collapsed: boolean) => writeLocal(NAV_KEY, collapsed ? "1" : "0"),
  profileSkipped: (accountId: number) => readLocal(`${PROFILE_SKIP_KEY}.${accountId}`) === "1",
  setProfileSkipped: (accountId: number) => writeLocal(`${PROFILE_SKIP_KEY}.${accountId}`, "1"),
};
