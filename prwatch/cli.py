"""pr-watch: one daemon that watches every registered Bitbucket pull request and wakes the Claude Code session that
created it when something needs a reaction (failed pipeline, new review comment, pipeline done so Sonar can be read).

The command line. The work is in the other modules: `cycle` (one look at each pull request), `events`, `wake`, `merging`,
`service` (processes), `store`, `config`, `bitbucket`.

Commands
  register   add a PR to the registry (done by Claude each time it creates a PR)
  ensure     start the daemon when it is not running (idempotent: only one instance ever runs)
  daemon     run the loop in the foreground (what `ensure` spawns)
  once       run a single cycle; `--dry-run` only prints what would be sent to Claude
  list       show the registry
  settings   show or change what is done for one PR
  unregister remove a PR
  ui         open the web dashboard (every system); starts it when needed; `--select REPO#N [--pulse]` shows a PR
  tray       the status icon (Linux, GTK 3): a native menu with what asks for something
  profile    `profile load FILE` loads the settings of a team (Bitbucket workspace, Jira, Sonar...)
  doctor     check what PR Watch needs and say how to add what is missing
  resume     turn PR Watch back on after the Claude session ran out of tokens
  merge      merge a PR through the API
  stop       stop the daemon
  install-desktop / install-launcher   register PR Watch as an application of this system

Credentials come from the environment of the process that starts the daemon: BITBUCKET_EMAIL and BITBUCKET_TOKEN.
They are never written to the log or the registry."""

import argparse

from prwatch.actions import options as pwoptions
from prwatch.core import config as pwconfig
from prwatch.engine import cycle as pwcycle
from prwatch.system import desktop as pwdesktop
from prwatch.engine import merging as pwmerging
from prwatch.system import processes as pwprocesses
from prwatch.engine import registration as pwregistration
from prwatch.engine import service as pwservice
from prwatch.core import permissions as pwpermissions
from prwatch.core import store as pwstore

def _settings_arguments(parser):
    parser.add_argument('--auto-push', choices=['yes', 'no', 'default'], help='whether Claude pushes its fixes to the branch of this PR')
    parser.add_argument('--auto-merge', choices=[m for m, _ in pwconfig.AUTO_MERGE_MODES] + ['default'], help='merge this PR by itself: off, ready (as soon as it can be merged) or resolved (and no comment left to handle)')
    parser.add_argument('--handling', metavar='CATEGORY=MODE[,...]', help='what is done per kind of event: ' + '; '.join(f"{c}: {'|'.join(m for m, _ in modes)}" for c, (_l, modes) in pwconfig.CATEGORIES.items()))


def settings_from_arguments(args):
    """The per-PR `settings` the command-line options describe ({} when none was given)."""
    settings = {}
    if args.auto_push:
        settings['autoPush'] = {'yes': True, 'no': False, 'default': None}[args.auto_push]
    if getattr(args, 'auto_merge', None):
        settings['autoMerge'] = None if args.auto_merge == 'default' else args.auto_merge
    if args.handling:
        pairs = [item.split('=', 1) for item in args.handling.split(',') if item.strip()]
        if any(len(pair) != 2 for pair in pairs):
            raise SystemExit('--handling expects CATEGORY=MODE[,CATEGORY=MODE...]')
        try:
            settings['handling'] = pwconfig.validate_handling({k.strip(): (None if v.strip() == 'default' else v.strip()) for k, v in pairs}, partial=True)
        except ValueError as error:
            raise SystemExit(str(error))
    return settings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    reg = sub.add_parser('register')
    reg.add_argument('--pr', type=int, required=True)
    reg.add_argument('--repo', help='default: the Bitbucket repository of `origin` in the current directory')
    reg.add_argument('--branch', help='default: the current branch')
    reg.add_argument('--work-dir', help='directory where the fixes are made (default: the git root of the current directory)')
    reg.add_argument('--session', help='id of the Claude session to resume (default: CLAUDE_CODE_SESSION_ID, set in a Claude Code session)')
    reg.add_argument('--session-cwd', help='directory the session was started in (default: read from the transcript of the session)')
    reg.add_argument('--since-pipeline', type=int, help='pipelines up to this build number are ignored (default: current)')
    _settings_arguments(reg)
    settings = sub.add_parser('settings', help='show or change what is done for one PR')
    settings.add_argument('--repo', required=True)
    settings.add_argument('--pr', type=int, required=True)
    settings.add_argument('--reset', action='store_true', help='forget the choices of this PR (back to the global ones)')
    _settings_arguments(settings)
    unreg = sub.add_parser('unregister')
    unreg.add_argument('--repo', required=True)
    unreg.add_argument('--pr', type=int, required=True)
    merge = sub.add_parser('merge')
    merge.add_argument('--repo', required=True)
    merge.add_argument('--pr', type=int, required=True)
    merge.add_argument('--strategy', default=None, choices=['squash', 'merge_commit', 'fast_forward', 'squash_fast_forward', 'rebase_fast_forward', 'rebase_merge'],
                       help="default: the one set in Bitbucket for the destination branch when known, else rebase_fast_forward")
    merge.add_argument('--keep-branch', action='store_true')
    tray = sub.add_parser('tray', help='the status icon (Linux, GTK 3); hover shows the summary in an anchored GNOME panel when available')
    tray.add_argument('--background', action='store_true', help='start the icon without opening the window')
    web = sub.add_parser('web', help='run the web dashboard server (`ui` starts it for you)')
    web.add_argument('action', choices=['serve'])
    web.add_argument('port', nargs='?', type=int)
    ui = sub.add_parser('ui', help='open the window of PR Watch (the web dashboard, on every system)')
    ui.add_argument('--select', metavar='REPO#PR', help='show this pull request')
    ui.add_argument('--pulse', action='store_true', help='with --select: only pulse the pull request for a moment, as the status icon does')
    ui.add_argument('--no-open', action='store_true', help='start the web dashboard and only print its address')
    once = sub.add_parser('once')
    once.add_argument('--dry-run', action='store_true')
    profile = sub.add_parser('profile', help='load the team settings (Bitbucket workspace, Jira, Sonar, automatic review...) kept in a JSON file')
    profile.add_argument('action', choices=['load'])
    profile.add_argument('path', help='the JSON file, e.g. profiles/<team>.json of the clone')
    sub.add_parser('resume', help='turn PR Watch back on after the Claude session ran out of tokens')
    sub.add_parser('doctor', help='check what PR Watch needs (tokens, scopes, commands) and say how to add what is missing')
    for name in ('ensure', 'daemon', 'list', 'stop', 'install-desktop', 'install-launcher'):
        sub.add_parser(name)
    args = parser.parse_args()

    if args.command == 'register':
        if pwconfig.missing_setup(pwconfig.config()):
            raise SystemExit('Bitbucket workspace not set: fill it in the Options › Équipe tab of the pr-watch window, or bitbucketWorkspace in config.json')
        guessed = pwregistration.detect_defaults()
        given = {'repo': args.repo, 'branch': args.branch, 'work_dir': args.work_dir, 'session': args.session, 'session_cwd': args.session_cwd}
        chosen = {key: value or guessed.get(key) for key, value in given.items()}
        missing = [f"--{key.replace('_', '-')}" for key, value in chosen.items() if not value]
        if missing:
            raise SystemExit(f"cannot tell {', '.join(missing)} from here: run the command in the clone of the pull request from a Claude Code session, or pass them")
        entry = pwregistration.register_pr(chosen['repo'], args.pr, chosen['branch'], chosen['work_dir'], chosen['session'], chosen['session_cwd'],
                            since_pipeline=args.since_pipeline, settings=settings_from_arguments(args), origin=pwprocesses.origin_of_this_process())
        print(f"registered {pwstore.key_of(entry)} (comments after {entry['lastComment']}, pipelines after {entry['lastPipeline']})")
    elif args.command == 'doctor':
        cfg = pwconfig.config()
        checks = pwpermissions.run(cfg['jiraSite'], cfg['claudeBinary'])
        print(pwpermissions.describe(checks))
        raise SystemExit(1 if any(c['required'] and not c['ok'] for c in checks) else 0)
    elif args.command == 'settings':
        target = next((e for e in pwstore.load_registry() if (e['repo'], e['pr']) == (args.repo, args.pr)), None)
        if not target:
            raise SystemExit('unknown pull request')
        change = settings_from_arguments(args)
        if args.reset:
            pwstore.update_entry(target, settings={})
        elif change:
            pwstore.update_entry(target, settings=pwconfig.merge_settings(target, change))
        shown = pwconfig.effective(target, pwconfig.config())
        print(f"{pwstore.key_of(target)}: autoPush={shown['autoPush']} autoMerge={shown['autoMerge']}")
        for name, mode in shown['handling'].items():
            own = ((target.get('settings') or {}).get('handling') or {}).get(name)
            print(f"  {name:<11} {mode:<7}{'  (own choice)' if own else ''}")
    elif args.command == 'profile':
        done = pwoptions.load_profile(args.path)
        print(done['message'])
        raise SystemExit(0 if done['ok'] else 1)
    elif args.command == 'unregister':
        with pwstore.registry_lock():
            pwstore.save_registry([e for e in pwstore.load_registry() if (e['repo'], e['pr']) != (args.repo, args.pr)])
    elif args.command == 'list':
        for e in pwstore.load_registry():
            print(pwstore.key_of(e), e.get('state'), e['branch'], f"session={e['sessionId']}", f"attempts={e.get('attempts', 0)}")
    elif args.command == 'merge':
        target = next((e for e in pwstore.load_registry() if (e['repo'], e['pr']) == (args.repo, args.pr)), None)
        print(pwmerging.merge_pr(target or {'repo': args.repo, 'pr': args.pr}, args.strategy or (target or {}).get('mergeDefault') or 'rebase_fast_forward', not args.keep_branch)[1])
    elif args.command == 'once':
        pwcycle.cycle(dry_run=args.dry_run)
    elif args.command == 'ensure':
        pwservice.ensure()
    elif args.command == 'resume':
        print('PR Watch réactivé.' if pwstore.resume() else 'PR Watch n’était pas désactivé.')
    elif args.command == 'stop':
        pwservice.stop_daemon()
    elif args.command == 'install-desktop':
        pwdesktop.install_desktop()
    elif args.command == 'install-launcher':
        pwdesktop.install_launcher()
    elif args.command == 'ui':
        pwservice.open_dashboard(open_window=not args.no_open, select=args.select, pulse=args.pulse)
    elif args.command == 'tray':
        raise SystemExit(pwservice.tray_main(['--background'] if args.background else []))
    elif args.command == 'web':
        from prwatch.web import server as dashboard

        dashboard.serve(args.port)
    elif args.command == 'daemon':
        pwservice.run_daemon()
