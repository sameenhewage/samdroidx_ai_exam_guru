import Link from "next/link";

export type AdminRole = "admin" | "reviewer";

type PrimaryAdminArea =
  | "home"
  | "materials"
  | "generate-papers"
  | "review-approve"
  | "published-papers";
type AdvancedAdminArea =
  | "analytics"
  | "blueprints"
  | "curriculum"
  | "documents"
  | "generation"
  | "knowledge"
  | "operations"
  | "papers"
  | "retrieval"
  | "review"
  | "validation";
export type AdminArea = PrimaryAdminArea | AdvancedAdminArea;

type AdminLink = Readonly<{
  adminOnly?: boolean;
  href: string;
  id: AdminArea;
  label: string;
}>;

const primaryAreas: readonly AdminLink[] = [
  { href: "/admin/home", id: "home", label: "Home" },
  { href: "/admin/materials", id: "materials", label: "Materials" },
  { href: "/admin/generate-papers", id: "generate-papers", label: "Generate Papers" },
  { href: "/admin/review-approve", id: "review-approve", label: "Review & Approve" },
  {
    href: "/admin/published-papers",
    id: "published-papers",
    label: "Published Papers",
  },
];

const advancedAreas: readonly AdminLink[] = [
  { href: "/admin/curriculum", id: "curriculum", label: "Curriculum" },
  { href: "/admin/documents", id: "documents", label: "Documents" },
  { href: "/admin/knowledge", id: "knowledge", label: "Knowledge records" },
  { href: "/admin/retrieval", id: "retrieval", label: "Knowledge / RAG" },
  { href: "/admin/analytics", id: "analytics", label: "Analytics" },
  { href: "/admin/blueprints", id: "blueprints", label: "Blueprints" },
  {
    href: "/admin/generation",
    id: "generation",
    label: "Generation diagnostics",
  },
  { href: "/admin/validation", id: "validation", label: "Validation details" },
  { href: "/admin/review", id: "review", label: "Review studio" },
  { href: "/admin/papers", id: "papers", label: "Paper studio" },
  {
    adminOnly: true,
    href: "/admin/operations",
    id: "operations",
    label: "Operations",
  },
];

const activeLinkClass =
  "rounded-md bg-white/10 px-3 py-2 text-sm font-semibold text-white outline-none focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950";
const linkClass =
  "rounded-md px-3 py-2 text-sm text-slate-300 outline-none hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950";

function AdminNavigationLink({ area, current }: { area: AdminLink; current: AdminArea }) {
  const active = area.id === current;
  return (
    <Link
      aria-current={active ? "page" : undefined}
      className={active ? activeLinkClass : linkClass}
      href={area.href}
    >
      {area.label}
    </Link>
  );
}

export function AdminHeader({ current, role }: { current: AdminArea; role: AdminRole }) {
  const availableAdvancedAreas = advancedAreas.filter(
    (area) => !area.adminOnly || role === "admin",
  );
  const advancedActive = availableAdvancedAreas.some((area) => area.id === current);

  return (
    <header className="border-b border-slate-300 bg-slate-950 px-5 py-5 text-white sm:px-8">
      <div className="mx-auto max-w-7xl">
        <div className="flex flex-wrap items-center justify-between gap-5">
          <div>
            <p className="font-mono text-xs tracking-[0.2em] text-amber-300 uppercase">
              AI Exam Guru
            </p>
            <p className="mt-1 font-semibold">Admin Content Studio</p>
          </div>

          <div className="flex items-center gap-4">
            <span className="rounded-full border border-white/20 px-3 py-1 text-xs capitalize">
              {role}
            </span>
            <form action="/api/auth/logout" method="post">
              <button
                className="rounded-sm text-sm text-slate-300 underline outline-none hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                type="submit"
              >
                Sign out
              </button>
            </form>
          </div>
        </div>

        <nav
          aria-label="Primary admin navigation"
          className="mt-5 flex w-full flex-wrap gap-1 border-t border-white/10 pt-4"
        >
          {primaryAreas.map((area) => (
            <AdminNavigationLink area={area} current={current} key={area.id} />
          ))}
        </nav>

        <details className="mt-3 border-t border-white/10 pt-3">
          <summary
            className={`w-fit cursor-pointer rounded-md px-3 py-2 text-sm outline-none hover:bg-white/10 hover:text-white focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 ${advancedActive ? "font-semibold text-white" : "text-slate-300"}`}
          >
            Advanced
          </summary>
          <nav
            aria-label="Advanced admin navigation"
            className="mt-2 grid gap-1 border-l border-white/10 pl-3 sm:grid-cols-2 lg:grid-cols-4"
          >
            {availableAdvancedAreas.map((area) => (
              <AdminNavigationLink area={area} current={current} key={area.id} />
            ))}
          </nav>
        </details>
      </div>
    </header>
  );
}
