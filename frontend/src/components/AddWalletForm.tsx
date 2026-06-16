"use client";

import { Plus } from "lucide-react";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { getPublicApiBaseUrl } from "@/lib/api";

const addressPattern = /^0x[a-fA-F0-9]{40}$/;

export function AddWalletForm() {
  const router = useRouter();
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const normalizedAddress = address.trim().toLowerCase();
    if (!addressPattern.test(normalizedAddress)) {
      setError("Address must be a 0x-prefixed 40 character hex wallet address.");
      return;
    }

    setIsSaving(true);
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/wallets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address: normalizedAddress,
          label: label.trim() || null,
          notes: notes.trim() || null,
          enabled: true,
        }),
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setError(payload?.detail ?? "Could not add wallet.");
        return;
      }

      setAddress("");
      setLabel("");
      setNotes("");
      router.refresh();
    } catch {
      setError("Could not reach the backend API. Check frontend/config/app.json and backend status.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-line bg-panel p-4 shadow-sm">
      <div className="grid gap-3 lg:grid-cols-[1.6fr_0.8fr_1fr_auto] lg:items-end">
        <label className="block">
          <span className="text-xs font-medium uppercase text-[#526070]">Wallet address</span>
          <input
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            placeholder="0x..."
            className="mt-1 h-10 w-full rounded-md border border-line bg-white px-3 text-sm outline-none focus:border-[#667085]"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium uppercase text-[#526070]">Label</span>
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Optional"
            className="mt-1 h-10 w-full rounded-md border border-line bg-white px-3 text-sm outline-none focus:border-[#667085]"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium uppercase text-[#526070]">Notes</span>
          <input
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Optional"
            className="mt-1 h-10 w-full rounded-md border border-line bg-white px-3 text-sm outline-none focus:border-[#667085]"
          />
        </label>
        <button
          type="submit"
          disabled={isSaving}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-ink px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-[#98a2b3]"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          {isSaving ? "Adding" : "Add"}
        </button>
      </div>
      {error ? <p className="mt-3 text-sm font-medium text-danger">{error}</p> : null}
    </form>
  );
}
