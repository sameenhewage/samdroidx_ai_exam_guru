import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

const TOKEN_COOKIE = "exam_guru_admin_token";
const ROLE_COOKIE = "exam_guru_admin_role";

export async function POST(request: NextRequest) {
  if (process.env.ENABLE_DETERMINISTIC_IDENTITY !== "true") {
    return NextResponse.json({ detail: { code: "development_login_disabled" } }, { status: 404 });
  }

  const form = await request.formData();
  const role = form.get("role");
  const token =
    role === "admin"
      ? process.env.DETERMINISTIC_ADMIN_TOKEN
      : role === "reviewer"
        ? process.env.DETERMINISTIC_REVIEWER_TOKEN
        : undefined;
  if (!token || (role !== "admin" && role !== "reviewer")) {
    return NextResponse.json({ detail: { code: "development_identity_unavailable" } }, { status: 503 });
  }

  const cookieStore = await cookies();
  cookieStore.set(TOKEN_COOKIE, token, {
    httpOnly: true,
    maxAge: 8 * 60 * 60,
    path: "/",
    sameSite: "strict",
    secure: process.env.ADMIN_COOKIE_SECURE === "true",
  });
  cookieStore.set(ROLE_COOKIE, role, {
    httpOnly: true,
    maxAge: 8 * 60 * 60,
    path: "/",
    sameSite: "strict",
    secure: process.env.ADMIN_COOKIE_SECURE === "true",
  });
  const appBaseUrl = process.env.APP_BASE_URL ?? request.url;
  return NextResponse.redirect(new URL("/admin/curriculum", appBaseUrl), 303);
}
