import "@fontsource-variable/ibm-plex-sans";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";

import type { Metadata } from "next";

import { AppShell } from "@/components/shell/AppShell";
import { THEME_INIT_SCRIPT } from "@/components/shell/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Copy Agent",
    template: "%s | Copy Agent",
  },
  description: "Monitoring and control for Hyperliquid copy trading.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the saved theme before the first paint to avoid a flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
