// The only module that touches browser storage. Everything else goes through these
// functions, so moving the session to another store (cookie, in-memory, an identity
// provider's SDK) is a change to this file alone.
//
// sessionStorage keeps the session across a page refresh and gives each browser tab its
// own, which is what lets two roles be shown side by side in a demo.

const TOKEN_KEY = "nba.session.token";
const CHALLENGE_KEY = "nba.signup.challenge";

const store = () => window.sessionStorage;

export const session = {
  token: (): string | null => store().getItem(TOKEN_KEY),
  start: (token: string) => store().setItem(TOKEN_KEY, token),
  end: () => store().removeItem(TOKEN_KEY),
};

/** A sign-up waiting for its one-time code. Holds no password and no session. */
export interface Challenge {
  verification_token: string;
  email: string;
  delivery: "email" | "development";
  expiresAt: number;
  resendAt: number;
  dev_otp?: string;
}

interface ChallengePayload {
  verification_token: string;
  email: string;
  delivery?: "email" | "development";
  expires_in?: number;
  resend_in?: number;
  dev_otp?: string;
}

export const pendingSignup = {
  get: (): Challenge | null => {
    const raw = store().getItem(CHALLENGE_KEY);
    return raw ? (JSON.parse(raw) as Challenge) : null;
  },
  /** Stores what the API returned; fields missing from a partial update keep their value. */
  save: (payload: ChallengePayload): Challenge => {
    const previous = pendingSignup.get();
    const now = Date.now();
    const challenge: Challenge = {
      verification_token: payload.verification_token,
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
