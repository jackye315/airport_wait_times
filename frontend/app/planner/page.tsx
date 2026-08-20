import type { Metadata } from "next";
import { PlannerClient } from "@/components/PlannerClient";

export const metadata: Metadata = { title: "Trip planner" };

export default function PlannerPage() {
  return <PlannerClient />;
}
