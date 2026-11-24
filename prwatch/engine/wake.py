"""Waking the Claude session of a pull request: the message, the run, the verdict it ends with, the checks of what it reports."""

import glob
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.core import store as pwstore
from prwatch.integrations import usage as pwusage
from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications

def session_file(entry):
    matches = glob.glob(str(Path.home() / '.claude' / 'projects' / '*' / f"{entry['sessionId']}.jsonl"))
    return matches[0] if matches else None


def session_busy(entry, cfg):
    path = session_file(entry)
    return bool(path) and time.time() - os.path.getmtime(path) < cfg['sessionBusySeconds']


def settle_threads(entry, pr, events, report):
    """Checks on Bitbucket what the report of the woken session says it did on the comment threads.

    The report is what the session believes it did: the user must not be told a reply was posted or a thread resolved
    when the PR shows otherwise. A thread the report calls resolved that has Claude's reply but is still open is resolved
    here; a thread with no reply is never resolved silently, it is reported. Returns (notes for the report, whether
    something the report claimed is missing).
    """
    claims_reply = re.search(r'répondu|réponse|replied|answered', report, re.IGNORECASE)
    claims_resolved = re.search(r'résolu|resolved', report, re.IGNORECASE)
    if not (claims_reply or claims_resolved):
        return [], False
    comments = pwbitbucket.all_comments(entry)
    by_id = {c['id']: c for c in comments}

    def root_of(comment):
        for _ in range(20):  # a thread is shallow; the bound only guards against a loop
            parent = (comment.get('parent') or {}).get('id')
            if not parent or parent not in by_id:
                return comment
            comment = by_id[parent]
        return comment

    author = pr['author']['uuid']
    asked = [i for e in events if e['kind'] in ('comment', 'review-bot') for i in ([e['comment']['id']] if e.get('comment') else []) + list(e.get('threads') or [])]
    notes, failed, seen = [], False, set()
    for comment_id in asked:
        if comment_id not in by_id:
            continue  # the comment of a review read in a pipeline log has no id on the PR
        root = root_of(by_id[comment_id])
        if root['id'] in seen:
            continue
        seen.add(root['id'])
        replied = any(c['id'] != root['id'] and (c.get('user') or {}).get('uuid') == author and not c.get('deleted') and root_of(c)['id'] == root['id'] for c in comments)
        resolved = root.get('resolution') is not None  # an empty object counts: it is what a resolved thread carries
        if not replied:
            failed = True
            notes.append(f"⚠ Le commentaire {root['id']} n'a reçu aucune réponse sur Bitbucket, malgré le rapport.")
        elif claims_resolved and not resolved:
            ok, _ = pwbitbucket.api_post(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/comments/{root['id']}/resolve")
            if ok:
                notes.append(f"ℹ PR Watch a résolu le fil du commentaire {root['id']} : le rapport l'annonçait résolu mais il était encore ouvert sur Bitbucket.")
                pwstore.log(f"{pwstore.key_of(entry)}: thread of comment {root['id']} was open, resolved by pr-watch")
            else:
                failed = True
                notes.append(f"⚠ Le fil du commentaire {root['id']} est encore ouvert sur Bitbucket, malgré le rapport : PR Watch n'a pas pu le résoudre (droits du jeton ?).")
    return notes, failed


class OutOfTokens(Exception):
    """The Claude session can not work any more: usage limit reached, no credit left."""


# What `claude -p` says when the plan or the credit is used up: waking it again would only fail again.
OUT_OF_TOKENS = re.compile(r'usage limit|limit reached|credit balance|out of (?:extra )?usage|rate.?limit|quota|insufficient (?:credit|funds)', re.I)


def out_of_tokens(text):
    """The line of `text` that says the session has no tokens left, '' when it does not say so."""
    return next((line.strip()[:200] for line in (text or '').splitlines() if OUT_OF_TOKENS.search(line)), '')


def build_prompt(entry, pr, events, cfg):
    body = '\n\n'.join(f"[{e['kind']}] {e['text']}" for e in events)
    sonar_rule = (
        f'- When the Sonar analysis of the PR is available, read the quality gate and the issues of the PR (pullRequest = {entry["pr"]}) with the Sonar MCP and fix the issues the change introduces. When an event gives a Sonar address, take the project key (`id`) and the `pullRequest` from it exactly: never guess the project key, a wrong one answers 404.' + '\n'
        if cfg.get('sonarPattern')
        else ''
    )
    push_rule = (
        'push the fix to the same branch.'
        if pwconfig.effective(entry, cfg)['autoPush']
        else 'do NOT push: the user pushes from the pr-watch window; say in your report what is waiting to be pushed.'
    )
    return f"""[pr-watch daemon] Automatic message about pull request #{entry['pr']} of {entry['repo']} ("{pr['title']}"), branch {entry['branch']} into {pr['destination']['branch']['name']}. This is not a message from the user: it comes from the daemon you asked to run when you created this PR.

Working directory of the change: {entry['workDir']}

What happened:
{body}

What to do (work autonomously: the user wants everything you can fix alone to be fixed without asking them anything; \
bother them only through the final PR-WATCH: ATTENTION line, and only for what you really cannot settle yourself):
- Fix what needs fixing in {entry['workDir']} on the branch {entry['branch']} (never rebase, never force-push). Run the lint and the related tests, commit with the ticket reference, then {push_rule}
- A failed pipeline whose log shows an infrastructure problem (registry authentication, network, runner) is not a code problem: say so, do not change code for it, and re-run the pipeline at most once.
- For a review comment: answer in French on the PR and resolve the thread when there is no debate, as your memory says; if the comment asks a question or a decision, answer it and leave the thread open.
- A comment by the author of the PR (the user) is not a review to be answered by default: it may give context about the PR to keep in mind for your next answers, reply to another comment (read it with that comment: it may settle it), or ask for a change. Do what it says; answer on the PR only when it expects an answer, and do not resolve a thread the user is still discussing.
- End every comment you post on the PR with the line `{cfg['claudeReplyMarker']}` (your replies carry the account of the user: this signature is how the daemon tells them from the user's own comments and does not wake you on them).
{sonar_rule}- An event of kind [user-decision] is the answer of the user to a reviewer's comment that asked something: do what it says (it states whether the user wants the comment handled, or what to do instead), and answer the reviewer on the PR in French when that makes sense.
- A comment of kind [review-bot] is the automatic review of the pipeline: fix the critical and major findings, judge the minor ones, and answer on the PR in French.
- Finish with a short report in French: what you saw, what you changed, what stays open. If nothing needs doing, say so in one line.
- The very last line of your answer is mandatory and read by the daemon: `PR-WATCH: OK` when everything is handled and nothing needs the user, or `PR-WATCH: ATTENTION <one sentence in French>` when the user must decide or do something (a question in a comment, a choice, a problem you could not fix alone, rights or infrastructure, a fix you chose not to push).
"""


def conclude(entry, pr, ok, report, events, cfg):
    """Reads the verdict the woken session ends its report with, and tells the user when they are needed."""
    found = re.findall(r'PR-WATCH:\s*(OK|ATTENTION)\s*(.*)', report)
    verdict, reason = found[-1] if found else ('ATTENTION', 'rapport sans conclusion explicite, à relire')
    if not ok:
        verdict, reason = 'ATTENTION', 'la session Claude a échoué ou a expiré'
    fields = {'lastReport': report[-3000:], 'lastReportAt': pwstore.now(), 'needsAttention': '',
              'reportsTotal': (entry.get('reportsTotal') or 0) + 1, 'reportsUnread': (entry.get('reportsUnread') or 0) + 1}
    if verdict == 'ATTENTION':
        fields['needsAttention'] = reason.strip() or 'à regarder'
        title, where = pr_notice(entry, pr, '⚠ Votre attention est nécessaire')
        notify(title, f"{where}\n{fields['needsAttention']}", cfg, urgent=True, entry=entry)
    else:
        title, where = pr_notice(entry, pr, '✔ Traitée par Claude')
        notify(title, f"{where}\n{len(events)} événement(s) traité(s)", cfg, entry=entry)
    return fields


def wake_claude(entry, pr, events, cfg, dry_run):
    prompt = build_prompt(entry, pr, events, cfg)
    if dry_run:
        print(f"--- would resume session {entry['sessionId']} in {entry['sessionCwd']} ---\n{prompt}")
        return True, ''
    # stream-json so that the quotas the CLI reports after the answer are kept (the answer itself is read from its last line)
    command = [cfg['claudeBinary'], '--resume', entry['sessionId'], '-p', *cfg['claudeArgs'], '--output-format', 'stream-json', '--verbose']
    pwstore.log(f"{pwstore.key_of(entry)}: resuming session {entry['sessionId']} for {len(events)} event(s)")
    try:
        result = subprocess.run(
            command,
            input=prompt,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            cwd=entry['sessionCwd'],
            timeout=cfg['claudeTimeoutSeconds'],
        )
    except subprocess.TimeoutExpired:
        pwstore.log(f'{pwstore.key_of(entry)}: claude timed out')
        return False, ''
    usage, final = pwusage.parse_stream(result.stdout)
    pwusage.record(usage)
    answer = (final or {}).get('result') if final else None
    text = answer if isinstance(answer, str) else ('' if final else result.stdout or '')
    failed = result.returncode != 0 or bool(final and final.get('is_error'))
    if failed and (reason := out_of_tokens(text + '\n' + (result.stdout or '') + '\n' + (result.stderr or ''))):
        raise OutOfTokens(reason)
    tail = text.strip()[-1500:]
    pwstore.log(f"{pwstore.key_of(entry)}: claude exited {result.returncode}\n{tail}" + (f"\nstderr: {result.stderr.strip()[-500:]}" if result.returncode else ''))
    return not failed, text


MAX_ACTIVITY_ITEMS = 10


def notify(title, body, cfg, urgent=False, entry=None):
    """Journals a notification and shows it; with `entry`, a click on it opens that pull request in the PR Watch window."""
    key = pwstore.key_of(entry) if entry and entry.get('repo') else None
    # in the journal the PR already has its own column: keep only what the notification says about it
    if key:
        # what the card shows as "something was done here" until the user has seen it
        previous = entry.get('activity')
        count = ((previous.get('count') or 1) if previous else 0) + 1
        now = int(time.time() * 1000)
        # what was done, kept for the tooltip of the card (the first line of the body only names the PR)
        done = {'text': title, 'detail': ' '.join(body.splitlines()[1:]), 'at': now}
        items = ((previous.get('items') or []) if previous else [])[-(MAX_ACTIVITY_ITEMS - 1):] + [done]
        pwstore.update_entry(entry, activity={'text': title, 'at': now, 'count': count, 'items': items})
    lines = body.splitlines()
    pwstore.log(f"{key + ': ' if key else ''}NOTIFY {title}: {' '.join(lines[1:]) if key and len(lines) > 1 else body}")
    if cfg['notify']:
        pwnotifications.notify(title, body, urgent, on_click=pwhost.module_command('ui', '--select', key) if key else None, group=key)


def pr_notice(entry, pr, headline):
    """Title and first line of a notification: what happened, then which pull request it is about."""
    where = f"{entry['repo']} #{entry['pr']}" if entry.get('repo') else f"PR {entry['pr']}"
    return headline, f"{where} — {pr['title']}" if pr and pr.get('title') else where


def create_session(repo, pr, branch, title, work_dir, cfg):
    """Starts a new Claude session for a pull request, in its clone: returns (session id, None) or (None, why it failed).

    The session only learns what the PR is; the daemon wakes it later, with the events, as it does for any other session.
    """
    session = str(uuid.uuid4())
    prompt = (
        f'[pr-watch] You take over pull request #{pr} of {repo} ("{title}"), branch {branch}; its local clone is {work_dir}. '
        'The pr-watch daemon will wake this session when a pipeline fails, a reviewer comments or Sonar reports a problem, and '
        'tell you what to do each time. For now change nothing and run nothing: answer with one short sentence in French '
        'saying you are ready.'
    )
    command = [cfg['claudeBinary'], '--session-id', session, '-p', *cfg['claudeArgs']]
    try:
        result = subprocess.run(command, input=prompt, encoding='utf-8', errors='replace', capture_output=True, cwd=work_dir, timeout=min(cfg['claudeTimeoutSeconds'], 180))
    except (OSError, subprocess.SubprocessError) as error:
        return None, f'Claude n’a pas pu démarrer : {error}'
    if result.returncode:
        return None, f"Claude a échoué ({result.returncode}) : {(result.stderr or result.stdout).strip()[-300:]}"
    pwstore.log(f'{repo}#{pr}: new session {session} created in {work_dir}')
    return session, None
