import { ErrorPanel } from "../ui";

/** Shown inside the shell when a page is not part of this account's workspace (403), or
 *  when there is no page at the address (404). */
export default function AccessDenied({ missing = false }: { missing?: boolean }) {
  return <ErrorPanel kind={missing ? "not_found" : "forbidden"} />;
}
