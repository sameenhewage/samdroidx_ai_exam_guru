import type { NextConfig } from "next";

import { securityHeaderRules } from "./src/lib/security-headers";
import { parseWebAppConfig } from "./src/lib/web-app-config";

const nextConfig: NextConfig = {
  async headers() {
    return securityHeaderRules(parseWebAppConfig());
  },
  output: "standalone",
};

export default nextConfig;
