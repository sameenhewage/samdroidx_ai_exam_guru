import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const cookieStore = await cookies();
  cookieStore.delete("exam_guru_admin_token");
  cookieStore.delete("exam_guru_admin_role");
  const appBaseUrl = process.env.APP_BASE_URL ?? request.url;
  return NextResponse.redirect(new URL("/admin/login", appBaseUrl), 303);
}
