#!/usr/bin/env bash
# Create the GitHub repo, push, and get the site live.
#
# Uses your own `gh` credentials on your own machine -- nothing here needs a
# token pasted anywhere.
#
#   ./scripts/bootstrap.sh my-repo-name [--private]
#
# Pages is deliberately set to build from GitHub Actions rather than from a
# branch. The dictionary shards are gitignored and built fresh into the Pages
# artifact on each deploy, so a branch-based deploy would serve a site with the
# Dictionary toggle permanently dead.

set -euo pipefail

REPO="${1:-sayable}"
VISIBILITY="--public"
[[ "${2:-}" == "--private" ]] && VISIBILITY="--private"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n%s\n' "$*" >&2; exit 1; }

command -v gh >/dev/null || die "The gh CLI is required: https://cli.github.com"
gh auth status >/dev/null 2>&1 || die "Not signed in. Run: gh auth login"
command -v python3 >/dev/null || die "python3 is required."

OWNER="$(gh api user --jq .login)"

# Before the first commit, not after. The snapshot that ships in the repo is
# seeded demo data, and a public repo keeps whatever you commit to it forever.
say "Building a first snapshot"
python3 -m pdgen generate
python3 -m pdgen dictionary build
python3 -m pdgen publish --min-confidence generated --include-taken
python3 - <<'PY'
import json, sys
if json.load(open("docs/data/domains.json"))["demo"]:
    sys.exit("docs/data/domains.json is still demo data. "
             "Run: python3 tools/seed_demo.py --clear")
PY

say "Creating ${OWNER}/${REPO}"
[[ -d .git ]] || { git init -q; git branch -M main; }
git add -A
git diff --staged --quiet || git commit -qm "Sayable: pronounceable domain finder"

if gh repo view "${OWNER}/${REPO}" >/dev/null 2>&1; then
  echo "  repo already exists, reusing it"
  git remote get-url origin >/dev/null 2>&1 || \
    git remote add origin "https://github.com/${OWNER}/${REPO}.git"
else
  gh repo create "${REPO}" ${VISIBILITY} --source=. --remote=origin --push
fi
git push -u origin main --quiet 2>/dev/null || true

say "Enabling Pages (source: GitHub Actions)"
# Idempotent: create it, or switch an existing site to the workflow builder.
gh api -X POST "repos/${OWNER}/${REPO}/pages" -f "build_type=workflow" >/dev/null 2>&1 \
  || gh api -X PUT "repos/${OWNER}/${REPO}/pages" -f "build_type=workflow" 2>&1 \
  || true
# Assert rather than hope. A branch-based deploy silently ships a site whose
# Dictionary toggle never works, and the old code swallowed every error.
BUILD_TYPE="$(gh api "repos/${OWNER}/${REPO}/pages" --jq .build_type 2>/dev/null || echo none)"
[[ "${BUILD_TYPE}" == "workflow" ]] \
  || die "Pages build_type is '${BUILD_TYPE}', not 'workflow'.
  Set it by hand: Settings > Pages > Source: GitHub Actions, then re-run."
echo "  build_type=workflow"

# gh issue create fails outright on a label that does not exist, so the sweep's
# failure handler would itself fail without this.
gh label create sweep-failure --color B60205 \
  --description "A sweep slice failed" >/dev/null 2>&1 || true

say "Seeding the database release"
python3 -m pdgen release push || echo "  skipped -- run 'pdgen release push' once you have data"

say "Done"
cat <<EOF

  Repo   https://github.com/${OWNER}/${REPO}
  Site   https://${OWNER}.github.io/${REPO}/   (pages.yml deploys on push, ~2 minutes)
  Agents https://${OWNER}.github.io/${REPO}/llms.txt

  The snapshot you just pushed is UNCHECKED -- every name in it is a guess.
  Prove the network path works, then do a real run.

  Call the parser directly. \`check --name google\` cannot work: it filters to
  names already in the db, and google is unreachable in every pattern (g, the
  gl cluster, and a trailing e are all excluded from the alphabet).

    python3 -c "from pdgen.check import rdap, RateLimiter as R; print(rdap('google','com',R(1)))"
    #   must print ('taken', [])   -- if it says available, RDAP parsing is broken
    python3 -c "from pdgen.check import doh_ns, RateLimiter as R; print(doh_ns('google.com',R(1)))"
    #   must print taken

    python3 -m pdgen plan --rdap-rps 2 --budget 1h
    python3 -m pdgen check --stage rdap --top 2000 --rdap-rps 2
    python3 -m pdgen publish --min-confidence rdap --fail-on-demo
    python3 -m pdgen release push && git add docs/ && git commit -m refresh && git push

  Then let the sweep take over (a bounded slice every 6h, resumable):
    gh workflow run sweep.yml -f duration=5m -f rdap_rps=1 -f chain_remaining=3

EOF
