import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../theme";
import { ForgotPasswordDialog, passwordRules } from "./ForgotPassword";

vi.mock("../auth", () => ({
  useAuth: () => ({ login: vi.fn() }),
  isChallenge: () => false,
}));

type Reply = { status: number; body: unknown };

function mockServer(replies: Record<string, Reply[]>) {
  const calls: Array<{ path: string; body: unknown }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    calls.push({ path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const queue = replies[path];
    const reply = queue?.shift() ?? { status: 200, body: {} };
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const STARTED = {
  status: 202,
  body: { message: "If this account is eligible...", delivery: "email", expires_in: 600, resend_in: 30 },
};
const REQUEST = "/api/auth/password-reset/request";
const VERIFY = "/api/auth/password-reset/verify";
const COMPLETE = "/api/auth/password-reset/complete";
const GOOD = "Recovered-pass-2026";

function renderDialog() {
  const onClose = vi.fn();
  const onDone = vi.fn();
  render(<ForgotPasswordDialog onClose={onClose} onDone={onDone} />);
  return { onClose, onDone, user: userEvent.setup() };
}

async function toCodeStep(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email/i), "person@example.org");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("group", { name: "Verification code" });
}

async function typeCode(user: ReturnType<typeof userEvent.setup>, code = "123456") {
  await user.click(screen.getByLabelText("Digit 1 of 6"));
  await user.keyboard(code);
}

beforeEach(() => {
  document.cookie = "nba_csrf=test-token";
  vi.unstubAllGlobals();
});

describe("ForgotPasswordDialog", () => {
  it("refuses a malformed email without calling the server", async () => {
    const calls = mockServer({});
    const { user } = renderDialog();
    await user.type(screen.getByLabelText(/email/i), "not-an-email");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Enter a valid email address.")).toBeTruthy();
    expect(calls).toHaveLength(0);
  });

  it("moves to the code step with a neutral message and a resend cooldown", async () => {
    const calls = mockServer({ [REQUEST]: [STARTED] });
    const { user } = renderDialog();
    await toCodeStep(user);
    expect(calls[0]).toEqual({ path: REQUEST, body: { email: "person@example.org" } });
    expect(screen.getByText(/can be recovered here, a code is on its way/)).toBeTruthy();
    const resend = screen.getByRole("button", { name: /Resend code in/ }) as HTMLButtonElement;
    expect(resend.disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("stays on the code step and shows the server's error for a wrong code", async () => {
    mockServer({
      [REQUEST]: [STARTED],
      [VERIFY]: [{ status: 400, body: { detail: { code: "otp_incorrect", message: "Incorrect code. 4 attempts left." } } }],
    });
    const { user } = renderDialog();
    await toCodeStep(user);
    await typeCode(user);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Incorrect code. 4 attempts left.")).toBeTruthy();
    expect(screen.getByRole("group", { name: "Verification code" })).toBeTruthy();
    expect(screen.queryByLabelText(/^New password/)).toBeNull();
  });

  it("never shows the password step without a successful server verification", async () => {
    mockServer({
      [REQUEST]: [STARTED],
      [VERIFY]: [{ status: 400, body: { detail: { code: "reset_expired", message: "This reset is no longer valid. Start again." } } }],
    });
    const { user } = renderDialog();
    await toCodeStep(user);
    await typeCode(user);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("This reset is no longer valid. Start again.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.queryByLabelText(/^New password/)).toBeNull();
  });

  it("enforces the password rules and confirmation, then confirms the change", async () => {
    const calls = mockServer({
      [REQUEST]: [STARTED],
      [VERIFY]: [{ status: 200, body: { verified: true } }],
      [COMPLETE]: [{ status: 200, body: { reset: true } }],
    });
    const { user, onDone, onClose } = renderDialog();
    await toCodeStep(user);
    await typeCode(user);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    const fresh = await screen.findByLabelText(/^New password/);
    const save = () => screen.getByRole("button", { name: "Save password" }) as HTMLButtonElement;

    await user.type(fresh, "short");
    expect(save().disabled).toBe(true);
    await user.clear(fresh);
    await user.type(fresh, GOOD);
    await user.type(screen.getByLabelText(/^Confirm new password/), "Different-1234");
    expect(screen.getByText("The two passwords do not match.")).toBeTruthy();
    expect(save().disabled).toBe(true);
    await user.clear(screen.getByLabelText(/^Confirm new password/));
    await user.type(screen.getByLabelText(/^Confirm new password/), GOOD);
    expect(save().disabled).toBe(false);
    await user.click(save());

    expect(await screen.findByText("Password changed")).toBeTruthy();
    expect(calls.at(-1)).toEqual({ path: COMPLETE, body: { password: GOOD, confirm_password: GOOD } });
    await user.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(onDone).toHaveBeenCalledWith("person@example.org");
    expect(onClose).toHaveBeenCalled();
  });

  it("returns to the start when the server refuses the reset authorization", async () => {
    mockServer({
      [REQUEST]: [STARTED],
      [VERIFY]: [{ status: 200, body: { verified: true } }],
      [COMPLETE]: [{ status: 401, body: { detail: { code: "reset_unauthorized", message: "Verify the code from your email first." } } }],
    });
    const { user } = renderDialog();
    await toCodeStep(user);
    await typeCode(user);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await user.type(await screen.findByLabelText(/^New password/), GOOD);
    await user.type(screen.getByLabelText(/^Confirm new password/), GOOD);
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("Verify the code from your email first.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
  });

  it("closes on Escape and is a labelled modal dialog", async () => {
    mockServer({});
    const { user, onClose } = renderDialog();
    const dialog = screen.getByRole("dialog", { name: "Reset your password" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("blocks a double submission while a request is in flight", async () => {
    let release: () => void = () => {};
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(String(input));
        await new Promise<void>((resolve) => (release = resolve));
        return new Response(JSON.stringify(STARTED.body), { status: 202, headers: { "content-type": "application/json" } });
      }),
    );
    const { user } = renderDialog();
    await user.type(screen.getByLabelText(/email/i), "person@example.org");
    const button = screen.getByRole("button", { name: "Continue" });
    await user.click(button);
    await user.click(screen.getByRole("button", { name: /Sending/ }));
    expect(calls).toHaveLength(1);
    release();
    await screen.findByRole("group", { name: "Verification code" });
  });
});

describe("password rules", () => {
  it("match the server policy", () => {
    expect(passwordRules("abc").every((r) => r.met)).toBe(false);
    expect(passwordRules("abcdefghij").every((r) => r.met)).toBe(false);
    expect(passwordRules("abcdefghi1").every((r) => r.met)).toBe(true);
  });
});

describe("Login page", () => {
  it("offers Forgot password and opens the dialog", async () => {
    const { default: Login } = await import("./Login");
    mockServer({});
    render(
      <ThemeProvider>
        <MemoryRouter>
          <Login />
        </MemoryRouter>
      </ThemeProvider>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Forgot password?" }));
    await waitFor(() => expect(screen.getByRole("dialog", { name: "Reset your password" })).toBeTruthy());
  });
});
