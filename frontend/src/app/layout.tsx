import type { Metadata } from "next";

import { AppShell } from "@/components/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: "Hyperliquid Copy Agent",
  description: "Internal execution, monitoring, and analytics dashboard for Hyperliquid copy trading.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
