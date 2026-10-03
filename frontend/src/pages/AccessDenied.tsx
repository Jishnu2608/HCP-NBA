import { Compass, ShieldX } from "lucide-react";
import { Link } from "react-router-dom";
import { ROLE_LABEL, useAuth } from "../auth";

export default function AccessDenied({ missing = false }: { missing?: boolean }) {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="mx-auto flex max-w-lg flex-col items-center py-16 text-center sm:py-24">
      <span
        className={
          missing
            ? "grid h-14 w-14 place-items-center rounded-2xl bg-subtle text-ink-muted"
            : "grid h-14 w-14 place-items-center rounded-2xl bg-bad-soft text-bad"
        }
        aria-hidden
      >
        {missing ? <Compass className="h-7 w-7" /> : <ShieldX className="h-7 w-7" />}
      </span>
      <h1 className="mt-5 text-2xl font-semibold tracking-[-0.015em] text-ink">
        {missing ? "Page not found" : "You don't have access to this page"}
      </h1>
      <p className="mt-2 text-[15px] leading-6 text-ink-muted">
        {missing
          ? "There is no page at this address. It may have moved, or the link may be incomplete."
          : `This page is not part of the ${ROLE_LABEL[user.role]} workspace. Access is decided by your account on the server, not by the address you open.`}
      </p>
      <Link
        to={user.home}
        className="mt-7 inline-flex min-h-11 items-center rounded-lg bg-primary px-5 text-sm font-semibold text-on-primary shadow-card transition-colors hover:bg-primary-hover"
      >
        Go to my workspace
      </Link>
    </div>
  );
}
