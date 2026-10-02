import { ShieldX } from "lucide-react";
import { Link } from "react-router-dom";
import { ROLE_LABEL, useAuth } from "../auth";

export default function AccessDenied({ missing = false }: { missing?: boolean }) {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="mx-auto mt-16 max-w-md text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-red-50 text-red-600">
        <ShieldX className="h-7 w-7" />
      </div>
      <h1 className="mt-4 text-xl font-semibold text-stone-900">
        {missing ? "Page not found" : "Access denied"}
      </h1>
      <p className="mt-2 text-sm text-stone-600">
        {missing
          ? "There is no page at this address."
          : `This page is not available to the ${ROLE_LABEL[user.role]} role. Access is decided by your account on the server, not by the address you open.`}
      </p>
      <Link
        to={user.home}
        className="mt-5 inline-flex rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
      >
        Back to my dashboard
      </Link>
    </div>
  );
}
