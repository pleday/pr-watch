"""One look at each watched pull request: what happened, what to do about it (wake Claude, ask the user, run a pipeline again), and the cycle over all of them."""

import re
import time

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.engine import events as pwevents
from prwatch.engine import merging as pwmerging
from prwatch.core import store as pwstore
from prwatch.engine import wake as pwwake
from prwatch.integrations import usage as pwusage

def handle(entry, cfg, dry_run):
    pr = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}")
    if not pr:
        return
    if entry.get('title') != pr['title'] and not dry_run:
        pwstore.update_entry(entry, title=pr['title'])
    strategies = pwmerging.merge_strategies(entry, pr)
    if not dry_run and (entry.get('mergeDefault'), entry.get('mergeAllowed')) != (strategies.get('default'), strategies.get('allowed')):
        pwstore.update_entry(entry, mergeDefault=strategies.get('default'), mergeAllowed=strategies.get('allowed'))
    if pr['state'] != 'OPEN':
        title, where = pwwake.pr_notice(entry, pr, 'Pull request fusionnée' if pr['state'] == 'MERGED' else 'Pull request refusée')
        pwwake.notify(title, where, cfg, entry=entry)
        if not dry_run:
            if pr['state'] == 'MERGED':
                pwmerging.mark_merged(entry, cfg)  # merged elsewhere (Bitbucket, a colleague): the same wait for the ticket
            else:
                pwstore.update_entry(entry, state='done', doneReason=pr['state'], doneAt=time.time())
        return
    pwmerging.sync_ready(entry, pr, cfg, dry_run)
    if pwmerging.auto_merge(entry, pr, cfg, dry_run):
        return  # merged: nothing left to look at
    pipelines, last_pipeline = pwevents.pipeline_events(entry)
    comments, last_comment = pwevents.comment_events(entry, pr, cfg)
    statuses, seen_statuses = pwevents.status_events(entry, pr)
    # what the user decided about the questions of the reviewers (answer-question in the interface): handled like any event
    decisions = [{'kind': 'user-decision', 'actionable': True, 'text': d['text']} for d in entry.get('decisions') or []]
    events = pipelines + comments + statuses + decisions
    forced = bool(entry.get('forceWake'))
    if forced:
        events.append(
            {
                'kind': 'manual',
                'actionable': True,
                'text': 'The user asked, from the pr-watch window, for a check of this PR now: look at the pipelines, the '
                'comments and Sonar, and handle what needs it.',
            }
        )
    actionable = [e for e in events if e['actionable']]
    cursors = {'lastPipeline': last_pipeline, 'lastComment': last_comment, 'seenStatuses': seen_statuses}
    settings = pwconfig.effective(entry, cfg)
    asked = [e for e in actionable if pwconfig.category(e) == 'question' and settings['handling'].get('question', 'ask') == 'ask']
    ask_user(entry, pr, cfg, asked, dry_run)
    suspended = bool(pwstore.suspension()) and not dry_run
    to_claude, to_rerun, to_tell = pwconfig.route([e for e in actionable if e not in asked], settings, claude_disabled=suspended)
    tell_events(entry, pr, cfg, to_tell, dry_run)
    if to_rerun and not to_claude and not forced:
        handle_infra(entry, pr, cfg, to_rerun, cursors, dry_run)
        return
    actionable = to_claude + to_rerun
    events = [e for e in events if e in actionable or not e['actionable']]
    if not actionable:
        if not dry_run:
            changes = dict(cursors)
            if forced:
                changes['forceWake'] = False
            if suspended:
                changes['decisions'] = []
            pwstore.update_entry(entry, **changes)
        return
    attempts = entry.get('attempts', 0)
    if attempts >= cfg['maxAttempts'] and not forced:
        if entry.get('state') != 'gave-up':
            title, where = pwwake.pr_notice(entry, pr, '⚠ Corrections automatiques arrêtées')
            pwwake.notify(title, f"{where}\n{attempts} essais utilisés : à regarder vous-même.", cfg, urgent=True, entry=entry)
            if not dry_run:
                pwstore.update_entry(entry, state='gave-up')
        return
    if not dry_run and not forced and pwwake.session_busy(entry, cfg):
        pwstore.log(f"{pwstore.key_of(entry)}: session in use, {len(actionable)} event(s) kept for later")
        return
    if not dry_run:
        usage_gate(cfg, measure=True)
    if pwstore.suspension() and not dry_run:
        _, _, deferred = pwconfig.route(to_claude, settings, claude_disabled=True)
        tell_events(entry, pr, cfg, deferred, dry_run)
        changes = dict(cursors)
        if forced:
            changes['forceWake'] = False
        if any(event.get('kind') == 'user-decision' for event in deferred):
            changes['decisions'] = []
        pwstore.update_entry(entry, **changes)
        return
    try:
        ok, report = pwwake.wake_claude(entry, pr, events, cfg, dry_run)
    except pwwake.OutOfTokens as error:
        # not an attempt, nothing is lost: the cursors do not move, so the same events come back after the reactivation
        pwstore.suspend(str(error))
        pwstore.log(f"{pwstore.key_of(entry)}: the Claude session has no tokens left, PR Watch is suspended ({error})")
        title, where = pwwake.pr_notice(entry, pr, '⏸ PR Watch désactivé : la session Claude n’a plus de jetons')
        pwwake.notify(title, f"{where}\nRéactivez-le depuis la fenêtre de PR Watch quand la session est de nouveau utilisable.", cfg, urgent=True)
        return
    if dry_run:
        return
    if ok:
        notes, failed = pwwake.settle_threads(entry, pr, actionable, report)
        if notes:
            report += '\n\n' + '\n'.join(notes)
            if failed and re.findall(r'PR-WATCH:\s*(OK|ATTENTION)', report)[-1] == 'OK':
                report += '\nPR-WATCH: ATTENTION ce que le rapport annonce sur les commentaires (réponse, résolution) ne figure pas sur Bitbucket'
    fields = pwwake.conclude(entry, pr, ok, report, actionable, cfg)
    if ok:
        pwstore.update_entry(entry, attempts=attempts + 1, forceWake=False, decisions=[], **cursors, **fields)
    else:
        pwstore.update_entry(entry, attempts=attempts + 1, forceWake=False, **fields)


def tell_events(entry, pr, cfg, events, dry_run):
    """Tells the user about events that cannot or should not be handled by Claude."""
    for event in events:
        category = pwconfig.category(event)
        label = pwconfig.CATEGORIES[category][0] if category else 'Demande manuelle'
        summary = event['text'].strip().splitlines()[0][:200]
        if dry_run:
            print(f"{pwstore.key_of(entry)}: would only notify ({label}): {summary}")
        else:
            title, where = pwwake.pr_notice(entry, pr, label)
            pwwake.notify(title, f"{where}\n{summary}", cfg, entry=entry)


def ask_user(entry, pr, cfg, events, dry_run):
    """Keeps the comments that ask something for the user to decide on (yes / no / other) and tells them once each."""
    if not events:
        return
    pending = list(entry.get('pendingQuestions') or [])
    known = {q['id'] for q in pending}
    fresh = [e['comment'] for e in events if e['comment']['id'] not in known]
    for comment in fresh:
        if dry_run:
            print(f"{pwstore.key_of(entry)}: would ask you what to do about the comment of {comment['author']}: {comment['text'][:120]}")
            continue
        pending.append({**comment, 'at': pwstore.now()})
        title, where = pwwake.pr_notice(entry, pr, '❓ Une question attend votre décision')
        summary = ' '.join(comment['text'].split())[:200]
        pwwake.notify(title, f"{where}\n{comment['author']} : {summary}", cfg, urgent=True, entry=entry)
    if fresh and not dry_run:
        asked = (entry.get('askedComments') or []) + [c['id'] for c in fresh]
        pwstore.update_entry(entry, pendingQuestions=pending, askedComments=asked[-200:])


def handle_infra(entry, pr, cfg, events, cursors, dry_run):
    """A pipeline broken by the infrastructure: run it again once per commit, then leave it to the user."""
    head = pr['source']['commit']['hash']
    numbers = ', '.join(f"#{e['pipeline']}" for e in events)
    if dry_run:
        print(f"{pwstore.key_of(entry)}: infrastructure failure of {numbers}; " + ('would re-run' if entry.get('rerunFor') != head else 'already re-run, would notify'))
        return
    if entry.get('rerunFor') != head:
        ok, message = pwbitbucket.rerun_pipeline(entry, pr)
        pwstore.log(f"{pwstore.key_of(entry)}: infrastructure failure of {numbers}, {message}")
        pwstore.update_entry(entry, rerunFor=head, **cursors)
        title, where = pwwake.pr_notice(entry, pr, 'Pipeline relancé')
        pwwake.notify(title, f"{where}\n{numbers} cassé par l'infrastructure ; {message}", cfg, entry=entry)
        return
    reason = f"Pipeline {numbers} en échec pour une raison d'infrastructure, déjà relancé une fois : à corriger côté Bitbucket ou du registre de paquets."
    pwstore.update_entry(entry, needsAttention=reason, **cursors)
    title, where = pwwake.pr_notice(entry, pr, '⚠ Votre attention est nécessaire')
    pwwake.notify(title, f"{where}\n{reason}", cfg, urgent=True, entry=entry)


def usage_gate(cfg, measure=False):
    """Suspends the wake-ups when the plan is almost used up, and starts them again once the window starts over.

    `measure` asks the CLI for fresh quotas when the last ones are old (done only right before a wake-up, so that an idle
    daemon spends nothing); otherwise the last measure is used.
    """
    current = pwstore.suspension()
    if current and current['kind'] == 'usage' and current['until'] and time.time() >= current['until']:
        pwstore.resume(override=False)  # the window the suspension waited for has started over: a measure will tell
        pwstore.log('PR Watch started again: the exhausted window started over')
        current = None
    if not cfg['usageGuard'] or pwstore.usage_override_active():
        return
    usage = pwusage.current(cfg['claudeBinary']) if measure else pwusage.read()[0]
    if usage is None:  # unknown: not a reason to stop
        return
    margins = {'session': cfg['usageSessionMarginPercent'], 'week': cfg['usageWeekMarginPercent']}
    reason, until = pwusage.exhausted(usage, margins)
    if reason and not current:
        pwstore.suspend(reason, 'usage', until)
        pwstore.log(f'PR Watch suspended to spare the overage credits: {reason}')
        pwwake.notify('⏸ PR Watch désactivé : forfait presque épuisé', f"{reason[0].upper() + reason[1:]}.\nIl repart tout seul à la remise à zéro ; « Réactiver » dans la fenêtre pour continuer quand même.", cfg, urgent=True)
    elif not reason and current and current['kind'] == 'usage':
        pwstore.resume(override=False)
        pwstore.log('PR Watch started again: the plan has room again')
        pwwake.notify('▶ PR Watch réactivé', 'Le forfait Claude a de nouveau de la marge.', cfg)


def cycle(dry_run=False):
    cfg = pwconfig.config()
    if pwconfig.missing_setup(cfg):
        if dry_run:
            print('Bitbucket workspace not set: fill it in Options › Équipe (or bitbucketWorkspace in config.json)')
        return cfg  # nothing to look at without a workspace; the window shows a banner asking for it
    if not dry_run and not pwconfig.schedule_effective_state(cfg)[0]:
        return cfg  # outside the hours: nothing is looked at, the events wait for the next opening
    if not dry_run:
        usage_gate(cfg)
    if not dry_run:
        pwmerging.refresh_ticket_waits(cfg)
        pwmerging.purge_done(cfg)
    with pwstore.registry_lock():
        entries = [e for e in pwstore.load_registry() if e.get('state', 'watching') == 'watching' or e.get('forceWake')]
    for entry in entries:
        if not dry_run and not pwconfig.schedule_effective_state(cfg)[0]:
            break
        try:
            handle(entry, cfg, dry_run)
        except Exception as error:  # one broken PR must not stop the others
            pwstore.log(f'{pwstore.key_of(entry)}: {type(error).__name__}: {error}')
    return cfg
