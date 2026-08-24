import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { guardBrowserRequest } from "@/lib/browser-request-guard";
import { parseWebAppConfig } from "@/lib/web-app-config";

export async function POST(request: NextRequest) {
  const config = parseWebAppConfig();
  const rejection = guardBrowserRequest(request, config);
  if (rejection) return rejection;

  const cookieStore = await cookies();
  cookieStore.delete("exam_guru_admin_token");
  cookieStore.delete("exam_guru_admin_role");
  return NextResponse.redirect(new URL("/admin/login", config.appBaseUrl), 303);
}
