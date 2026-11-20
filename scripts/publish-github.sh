#!/bin/sh
# Publishes commits of develop on GitHub: cherry-picks them onto the `github` branch (checked out in a worktree next to this
# folder), drops the internal ticket reference from the message, runs the tests, then pushes to github/main.
# Usage: scripts/publish-github.sh <commit>...      (PUSH=0 stops before the push)
set -eu
[ $# -gt 0 ] || { echo "usage: $0 <commit>..." >&2; exit 2; }
repo=$(git rev-parse --show-toplevel)
tree="$repo/../$(basename "$repo")-github"
[ -d "$tree" ] || git -C "$repo" worktree add "$tree" github
cd "$tree"
[ -z "$(git status --porcelain)" ] || { echo "$tree has uncommitted changes" >&2; exit 1; }
for commit in "$@"; do
  git cherry-pick "$commit" || { echo "Resolve the conflicts in $tree, 'git cherry-pick --continue', then run make test and push by hand." >&2; exit 1; }
  # The hook of the repository demands a ticket on every commit; the public history has none, so the amend skips it.
  git log -1 --format=%B | sed -E '1s/ \[[Tt]icket: ?[^]]*\]$//' | git commit --amend --no-verify -q -F -
done
make test >/dev/null
[ "${PUSH:-1}" = 0 ] && { echo "Stopped before the push (PUSH=0): $tree"; exit 0; }
git push --no-verify github refs/heads/github:refs/heads/main
