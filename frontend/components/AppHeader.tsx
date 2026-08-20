import Link from "next/link";

export function AppHeader() {
  return (
    <header className="app-header">
      <div className="header-inner">
        <Link className="brand" href="/dashboard" aria-label="AirPlanner dashboard">
          <span className="brand-mark" aria-hidden="true">✈</span>
          <span>Air<b>Planner</b></span>
        </Link>
        <nav aria-label="Primary navigation">
          <Link href="/dashboard">Dashboard</Link>
          <Link href="/planner">Trip planner</Link>
        </nav>
        <div className="live-indicator"><span /> Live collection</div>
      </div>
    </header>
  );
}
