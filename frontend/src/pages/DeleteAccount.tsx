// Permanent deletion of a patient account, used from the account drawer and from an erasure
// request. Two deliberate steps: open the confirmation, then type the account's email. The
// server checks the permission, that the account is a patient, and the typed email.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { useToast } from "../toast";
import { Alert, Button, ErrorNote, TextField } from "../ui";

export function DeleteAccount({
  account,
  privacyRequestId,
  onDeleted,
}: {
  account: { id: number; name: string; email: string };
  privacyRequestId?: number;
  onDeleted?: () => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const matches = typed.trim().toLowerCase() === account.email.toLowerCase();
  const remove = useMutation({
    mutationFn: () =>
      api(`/admin/users/${account.id}`, {
        method: "DELETE",
        body: { confirm_email: typed.trim(), ...(privacyRequestId ? { privacy_request_id: privacyRequestId } : {}) },
      }),
    onSuccess: () => {
      toast("Account and patient data deleted.");
      void client.invalidateQueries({ queryKey: ["users"] });
      void client.invalidateQueries({ queryKey: ["privacy-requests"] });
      onDeleted?.();
    },
  });

  if (!open) {
    return (
      <Button variant="quiet-danger" onClick={() => setOpen(true)}>
        <Trash2 className="h-4 w-4" aria-hidden /> Delete account permanently
      </Button>
    );
  }
  return (
    <div className="space-y-4 rounded-xl border border-bad-line bg-bad-soft/40 p-4">
      <Alert tone="bad" title="Delete this account permanently?">
        <p>
          {account.name}'s account is removed, with their own patient record: conditions, medications, refills, care
          requests, instructions, outreach consent and recommendations. This cannot be undone.
        </p>
        <p className="mt-2">
          The audit log keeps what happened but no longer names them. The same email can register again later and
          starts as a new, empty patient.
        </p>
      </Alert>
      <TextField
        label={
          <>
            Type <span className="break-all font-semibold">{account.email}</span> to confirm
          </>
        }
        value={typed}
        autoComplete="off"
        onChange={(e) => setTyped(e.target.value)}
      />
      <ErrorNote error={remove.error} />
      <div className="flex flex-wrap gap-2">
        <Button variant="danger" disabled={!matches} busy={remove.isPending} onClick={() => remove.mutate()}>
          <Trash2 className="h-4 w-4" aria-hidden /> Delete permanently
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
            setTyped("");
          }}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
