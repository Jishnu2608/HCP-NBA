// Work waiting for the signed-in account, by kind (`GET /api/me/attention`): open care
// requests for a care manager, consultations for an HCP, unread messages, requests for an
// administrator. Shown as counts next to menu items and in page banners, so nobody has to
// know which page to open to find new work. Refreshed every minute and when the window
// regains focus.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export function useAttention(): Record<string, number> {
  const query = useQuery({
    queryKey: ["attention"],
    queryFn: () => api<Record<string, number>>("/me/attention"),
    refetchInterval: 60_000,
  });
  const counts = query.data ?? {};
  return {
    ...counts,
    // The administrator's request centre: privacy requests plus specialty change requests.
    requests: (counts.privacy_requests ?? 0) + (counts.specialty_requests ?? 0),
  };
}
