import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Conciliación Bancaria — Army Technologies",
  description:
    "Sistema interno de conciliación bancaria entre el libro mayor del CRM y los extractos de banco.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
