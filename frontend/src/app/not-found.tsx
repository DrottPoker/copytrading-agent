import { ArrowLeft } from "lucide-react";

import { LinkButton } from "@/components/ui/primitives";

export default function NotFoundPage() {
  return (
    <section className="mx-auto mt-10 w-full max-w-lg rounded-lg border border-line bg-surface px-6 py-8">
      <p className="text-xs font-medium text-fg-3">404</p>
      <h1 className="mt-1 text-xl font-semibold">This page or wallet does not exist</h1>
      <p className="mt-2 text-sm text-fg-3">
        It may have been deleted, or the address in the link is wrong. Archived wallets are listed on the Wallets page.
      </p>
      <div className="mt-5 flex gap-2">
        <LinkButton href="/" variant="primary">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Overview
        </LinkButton>
        <LinkButton href="/wallets?view=archived">Archived wallets</LinkButton>
      </div>
    </section>
  );
}
