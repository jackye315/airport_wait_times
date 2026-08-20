import type { Metadata } from "next";
import { DashboardClient } from "@/components/DashboardClient";

export const metadata: Metadata = { title: "Airport dashboard" };

export default function DashboardPage() {
  return <DashboardClient />;
}
