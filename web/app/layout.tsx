import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GlobalCorp HR AI Assistant — ADK 2.1 Enterprise Workspace",
  description:
    "Grounded Multi-Agent HR & IT Assistant powered by Google ADK 2.1, Vertex AI Check Grounding, WorkWeek HRIS MCP, and ServiceImmediately ITSM MCP (WCAG 2.2 AA).",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark h-full">
      <head>
        <meta
          httpEquiv="Content-Security-Policy"
          content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        />
      </head>
      <body className="h-full overflow-hidden bg-slate-950 text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
