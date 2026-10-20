#!/usr/bin/env bash
# Build the Espresso Caribbean Next.js site as a static export served at
# /clients/espressocaribbean (behind the clients blueprint's password gate).
#
# The client's source is never modified: it's copied to a temp dir, the
# sub-path fixes from its HANDOVER.md (option B) are applied to the copy,
# and the `out/` export replaces app/client_sites/espressocaribbean/.
# Re-run whenever the client project changes, then commit + deploy.
#
#   scripts/build_client_espressocaribbean.sh ["S:/Caribeen coffee/04-development/src"]
set -euo pipefail
# Git Bash on Windows rewrites "/clients/..." arguments into Windows paths
# (D:/Git/clients/...) when calling native programs like node. Turn that off.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"

SRC="${1:-/s/Caribeen coffee/04-development/src}"
BASE="/clients/espressocaribbean"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO/app/client_sites/espressocaribbean"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "copying $SRC -> $WORK"
tar -C "$SRC" --exclude=node_modules --exclude=.next --exclude=out -cf - . | tar -C "$WORK" -xf -
cd "$WORK"

# patch FILE FROM TO -- literal replace, and fail loudly if FROM isn't there
# (the client source changed shape; fix this script rather than ship a
# build whose links point at the domain root).
patch() {
  node -e '
    const fs = require("fs");
    const [file, from, to] = process.argv.slice(1);
    const s = fs.readFileSync(file, "utf8");
    if (!s.includes(from)) { console.error("PATCH FAILED: " + file + " lacks " + JSON.stringify(from)); process.exit(1); }
    fs.writeFileSync(file, s.split(from).join(to));
  ' "$@"
}

cat > next.config.ts <<EOF
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  basePath: "$BASE",
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true, remotePatterns: [{ protocol: "https", hostname: "images.unsplash.com" }] },
};
export default nextConfig;
EOF

# Plain strings basePath doesn't rewrite (HANDOVER.md, option B step 2).
patch components/map/MapCanvas.tsx 'config.WORKER_URL = "/maplibre-gl-worker.mjs"' "config.WORKER_URL = \"$BASE/maplibre-gl-worker.mjs\""
patch lib/content/recipes.ts 'return `/qr/' "return \`$BASE/qr/"
patch components/sections/SiteHeader.tsx 'href={item.href}' "href={\"$BASE\" + item.href}"
patch components/sections/SiteFooter.tsx 'href={item.href}' "href={\"$BASE\" + item.href}"
patch components/ui/Button.tsx '      href={href}' "      href={href.startsWith(\"/\") ? \"$BASE\" + href : href}"

npm ci --no-audit --no-fund --loglevel=error
npx next build --turbopack

# Every root-relative URL in the export must carry the base path.
if grep -rqE '[A-Z]:/Git|href="/(recipes|#)' out; then
  echo "BUILD CHECK FAILED: un-prefixed or mangled links in out/" >&2
  grep -rnoE '[A-Z]:/Git[^"]*|href="/(recipes|#)[^"]*"' out | head >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"
cp -r out/. "$DEST/"
echo "built -> $DEST ($(du -sh "$DEST" | cut -f1))"
