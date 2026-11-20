"""What happened on a pull request that deserves a reaction: pipelines, comments, the automatic review, the Sonar quality gate."""

import datetime
import json
import re
import urllib.request
import uuid

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.core import store as pwstore

def needs_decision(text, cfg):
    """Whether a reviewer's comment asks a question or a validation (`questionPatterns`)."""
    for pattern in cfg['questionPatterns']:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error as error:
            pwstore.log(f'ignored questionPatterns entry {pattern!r}: {error}')
    return False


NOISE = re.compile(r'^(\(Reading database|Selecting|Preparing to unpack|Unpacking|Setting up|Get:|Fetched|Hit:|Processing triggers|running python)')


ERROR = re.compile(r'error|fail|invalid|denied|cannot|unable|exception|ERR!|YN\d{4}|exit code|✖', re.IGNORECASE)


def summarize_log(text, limit=4500):
    """What matters of a step log: the error lines (repeated warnings of the same code collapsed), then the tail."""
    lines, seen = [], {}
    for line in text.splitlines():
        if NOISE.match(line):
            continue
        code = re.match(r'➤ (YN\d+):', line)
        if code:
            seen[code.group(1)] = seen.get(code.group(1), 0) + 1
            if seen[code.group(1)] > 3:
                continue
        lines.append(line)
    errors = [line for line in lines if ERROR.search(line)][:25]
    tail = [line for line in lines[-30:] if line not in errors]
    extra = [f'({count} lines of {code} in total)' for code, count in seen.items() if count > 3]
    return ('\n'.join(errors) + '\n[...]\n' + '\n'.join(tail) + '\n' + '\n'.join(extra))[-limit:]


def step_result(step):
    state = step.get('state') or {}
    return (state.get('result') or {}).get('name', state.get('name', '?'))


SONAR_GATE = re.compile(r'QUALITY GATE STATUS:\s*(PASSED|FAILED)(?:\s*-\s*View details on\s*(\S+))?')


def sonar_target(url):
    """(project key, pull request) of a Sonar dashboard address such as .../dashboard?id=org.x%3Aname&pullRequest=135."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url or '').query)
    return (query.get('id') or [None])[0], (query.get('pullRequest') or [None])[0]


def sonar_gate(entry, uuid, steps, cfg=None):
    """What the Sonar step of a pipeline says about the quality gate: (PASSED | FAILED | None, dashboard address).

    The scanner prints `QUALITY GATE STATUS: PASSED - View details on <address>` in the log of its step, with the exact project
    key: that is more reliable than asking the woken session to look the analysis up (it guessed the key and got a 404).
    """
    for step in steps:
        if pwconfig.sonar_matches(cfg or pwconfig.config(), step.get('name', '')) and step_result(step) == 'SUCCESSFUL':
            text = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pipelines/{uuid}/steps/{urllib.parse.quote(step['uuid'])}/log", raw=True) or ''
            found = SONAR_GATE.findall(text)
            if found:
                return found[-1][0], found[-1][1] or None
    return None, None


def sonar_instruction(url):
    key, number = sonar_target(url)
    if not key:
        return ''
    return f" Use exactly the project key '{key}'" + (f" and pullRequest '{number}'" if number else '') + ' of this address with the Sonar MCP: do not guess them.'


def review_from_log(text):
    """The final message of an AI review step, whose log is a stream of JSON lines (`agent_message_chunk` pieces per message)."""
    messages = {}
    for line in text.splitlines():
        if not line.startswith('{"sessionId"'):
            continue
        try:
            update = json.loads(line)['update']
        except (ValueError, KeyError):
            continue
        if update.get('sessionUpdate') == 'agent_message_chunk':
            messages[update.get('messageId')] = messages.get(update.get('messageId'), '') + (update.get('content') or {}).get('text', '')
    return list(messages.values())[-1].strip() if messages else ''


def review_posted(entry, pipeline, cfg):
    """Whether the review of this pipeline reached the PR as a comment (followed bot comment created after the pipeline began)."""
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/comments", {'pagelen': 100, 'sort': '-id'}) or {}
    for comment in data.get('values', []):
        user = comment.get('user') or {}
        text = (comment.get('content') or {}).get('raw', '')
        if user.get('type') == 'app_user' and (comment.get('created_on') or '') >= pipeline.get('created_on', ''):
            if any(user.get('display_name') == r['author'] and text.startswith(r.get('startsWith', '')) for r in cfg['followBotComments']):
                return True
    return False


def review_event(entry, pipeline, steps, uuid):
    """An event for the verdict of the AI review when the step succeeded but posted no comment, else None."""
    cfg = pwconfig.config()
    pattern = cfg['reviewStepPattern']
    step = next((s for s in steps if pattern and re.search(pattern, s.get('name', ''), re.IGNORECASE) and step_result(s) == 'SUCCESSFUL'), None)  # '' would match every step
    if not step or review_posted(entry, pipeline, cfg):
        return None
    text = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pipelines/{uuid}/steps/{urllib.parse.quote(step['uuid'])}/log", raw=True) or ''
    verdict = review_from_log(text)
    ignored = [r.get('ignoreIfContains') for r in cfg['followBotComments'] if r.get('ignoreIfContains')]
    if not verdict or any(i in verdict for i in ignored):
        return None
    return {
        'kind': 'review-bot',
        'actionable': True,
        'text': f"The automatic review of pipeline #{pipeline['build_number']} posted no comment on the PR; its verdict, read in the log of the step (it may be in English: your answers on the PR stay in French):\n" + verdict,
        'comment': {'id': 0, 'author': 'Revue automatique (journal du pipeline)', 'place': '', 'text': verdict},
    }


def pipeline_events(entry):
    data = pwbitbucket.api(
        f"{pwbitbucket.repo_path(entry)}/pipelines/",
        {'target.branch': entry['branch'], 'sort': '-created_on', 'pagelen': 10},
    ) or {}
    events, newest = [], entry.get('lastPipeline', 0)
    # A pipeline of the branch still running (the one of the last commit, say) is about to publish its own Sonar analysis:
    # until it is over, the analysis of the PR is missing or stale, so a success is not looked at yet.
    running = any(p['state']['name'] != 'COMPLETED' for p in data.get('values', []))
    for pipeline in sorted(data.get('values', []), key=lambda p: p['build_number']):
        number = pipeline['build_number']
        if number <= entry.get('lastPipeline', 0):
            continue
        if pipeline['state']['name'] != 'COMPLETED':
            # Cursors only move over finished pipelines, so a running one is looked at again later.
            break
        uuid = urllib.parse.quote(pipeline['uuid'])
        steps = (pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pipelines/{uuid}/steps/") or {}).get('values', [])
        result = (pipeline['state'].get('result') or {}).get('name', '?')
        summary = ', '.join(f"{s.get('name', '?')}={step_result(s)}" for s in steps)
        sonar = [s for s in steps if pwconfig.sonar_matches(pwconfig.config(), s.get('name', '')) and step_result(s) == 'SUCCESSFUL']
        if result == 'SUCCESSFUL' and sonar and running:
            break  # looked at again once nothing runs any more
        newest = max(newest, number)
        if result == 'SUCCESSFUL':
            review = review_event(entry, pipeline, steps, uuid)
            if review:
                events.append(review)
            gate, address = sonar_gate(entry, uuid, steps) if sonar else (None, None)
            if gate == 'PASSED':
                # the scanner itself says the gate passed: nothing for the session to do, so it is not woken
                events.append({'kind': 'pipeline-ok', 'actionable': False, 'text': f'Pipeline #{number} succeeded ({summary}). Sonar quality gate PASSED ({address or "no address"}).'})
            elif gate == 'FAILED':
                events.append({'kind': 'pipeline-ok', 'actionable': True,
                               'text': f'Pipeline #{number} succeeded ({summary}) but the Sonar quality gate FAILED: {address or "no address given"}.'
                                       f' Read the issues of the PR and fix the ones the change introduces.{sonar_instruction(address)}'})
            else:
                events.append(
                    {
                        'kind': 'pipeline-ok',
                        'actionable': bool(sonar),
                        'text': f'Pipeline #{number} succeeded ({summary}).'
                        + (' The Sonar analysis of the PR ran: read the quality gate and the issues of the PR.' if sonar else ''),
                    }
                )
            continue
        failed = [s for s in steps if step_result(s) == 'FAILED']
        details, texts = [], []
        for step in failed:
            text = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pipelines/{uuid}/steps/{urllib.parse.quote(step['uuid'])}/log", raw=True) or ''
            texts.append(text)
            details.append(f"--- log of failed step '{step.get('name', '?')}' (tail) ---\n{summarize_log(text)}")
        patterns = pwconfig.config()['infraPatterns']
        infra = bool(texts) and all(any(pattern in text for pattern in patterns) for text in texts)
        events.append(
            {
                'kind': 'pipeline-failed',
                'actionable': True,
                'infra': infra,
                'pipeline': number,
                'text': f'Pipeline #{number} FAILED ({summary}).\n' + '\n'.join(details),
            }
        )
    return events, newest


def followed_bot(user, text, cfg):
    """Whether a comment of an application account is one to react to (see `followBotComments`)."""
    for rule in cfg['followBotComments']:
        if user.get('display_name') == rule['author'] and text.startswith(rule.get('startsWith', '')):
            ignored = rule.get('ignoreIfContains')
            return not (ignored and ignored in text)
    return False


REVIEW_WINDOW_SECONDS = 600


def review_threads(summary, comments):
    """The inline comments the application account posted together with `summary`, still open: each is a thread of its own.

    The automatic review posts a summary and one inline comment per finding. Only the summary is followed, so the others
    are given to the woken session with it: it would otherwise answer on the summary and leave the findings untouched.
    """
    def when(comment):
        try:
            return datetime.datetime.fromisoformat(comment.get('created_on', '').replace('Z', '+00:00'))
        except ValueError:
            return None

    start, found = when(summary), []
    user = (summary.get('user') or {}).get('uuid')
    for other in comments:
        inline = other.get('inline') or {}
        if other['id'] == summary['id'] or (other.get('user') or {}).get('uuid') != user or not inline.get('path'):
            continue
        if other.get('deleted') or other.get('parent') or other.get('resolution') is not None:
            continue
        moment = when(other)
        if start and moment and abs((moment - start).total_seconds()) <= REVIEW_WINDOW_SECONDS:
            found.append({'id': other['id'], 'place': f"{inline['path']}:{inline.get('to') or inline.get('from')}", 'text': (other.get('content') or {}).get('raw', '')})
    return found


def comment_events(entry, pr, cfg):
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/comments", {'pagelen': 100, 'sort': 'id'}) or {}
    author = pr['author']['uuid']
    events, newest = [], entry.get('lastComment', 0)
    for comment in data.get('values', []):
        if comment['id'] <= entry.get('lastComment', 0):
            continue
        newest = max(newest, comment['id'])
        if comment['id'] in (entry.get('askedComments') or []):
            continue  # already put to the user, who may have answered since: not to be asked again
        user = comment.get('user') or {}
        text = (comment.get('content') or {}).get('raw', '')
        bot = user.get('type') == 'app_user'
        from_author = user.get('uuid') == author
        if comment.get('deleted') or (from_author and cfg['claudeReplyMarker'] in text):
            continue  # a reply of Claude itself, posted with the account of the author
        if bot and not followed_bot(user, text, cfg):
            continue
        if user.get('display_name') in cfg['ignoreCommentAuthors']:
            continue
        inline = comment.get('inline') or {}
        place = f" on {inline['path']}:{inline.get('to') or inline.get('from')}" if inline.get('path') else ''
        parent = f" (reply to comment {comment['parent']['id']})" if comment.get('parent') else ''
        siblings = review_threads(comment, data.get('values', [])) if bot else []
        extra = ''.join(
            f"\n\nInline comment {t['id']} of the same review, on {t['place']} (its own thread: answer it there, and resolve it when there is no debate):\n{t['text']}"
            for t in siblings
        )
        events.append(
            {
                # the author is the user: what they write is context or an instruction, never a question to put back to them
                'kind': 'review-bot' if bot else ('question' if not from_author and needs_decision(text, cfg) else 'comment'),
                'actionable': True,
                'threads': [t['id'] for t in siblings],
                'text': f"Comment {comment['id']} by {user.get('display_name', '?')}{' (the author of the PR, i.e. the user)' if from_author else ''}{place}{parent}:\n" + text + extra,
                'comment': {'id': comment['id'], 'author': user.get('display_name', '?'), 'place': place.strip(), 'text': text},
                'parent': (comment.get('parent') or {}).get('id'),
            }
        )
    return events, newest


def status_events(entry, pr):
    """A Sonar quality gate that failed on the latest commit (the build status Sonar reports to Bitbucket)."""
    head = pr['source']['commit']['hash']
    # The statuses of the PR itself: the ones of the commit need a repository-read scope the token does not have.
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/statuses", {'pagelen': 50}) or {}
    seen, events = list(entry.get('seenStatuses', [])), []
    for status in data.get('values', []):
        label = f"{status.get('key', '')} {status.get('name', '')}".lower()
        if not pwconfig.sonar_matches(pwconfig.config(), label) or status.get('state') != 'FAILED':
            continue
        marker = f"{head}:{status.get('key')}:FAILED"
        if marker in seen:
            continue
        seen.append(marker)
        events.append(
            {
                'kind': 'sonar-failed',
                'actionable': True,
                'text': f"The Sonar status '{status.get('name') or status.get('key')}' of the latest commit {head} is FAILED: "
                f"{status.get('description') or ''} {status.get('url') or ''}\nRead the quality gate and the issues of the PR "
                f"with the Sonar MCP and fix the ones the change introduces." + (sonar_instruction(status.get('url')) or f" (pullRequest = {entry['pr']})"),
            }
        )
    return events, seen[-30:]
