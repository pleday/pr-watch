"""The Claude Chat plugins of the IDEs of this machine: which version is installed, which is the latest, and the update.

Releases are read from the GitHub repository of the plugin: `vX.Y.Z` carries the zip of the JetBrains plugin,
`vscode-vX.Y.Z` the .vsix of the VS Code extension. Nothing downloaded is ever run from here: the zip is only unpacked into
the plugin folder (after checking its paths), the .vsix is handed to `code --install-extension`.
"""
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile

from prwatch.system import editors as pweditors

REPO = 'pleday/claudechat'
RELEASES = f'https://api.github.com/repos/{REPO}/releases?per_page=30'
TAGS = {'jetbrains': re.compile(r'^v(\d+(?:\.\d+)*)$'), 'vscode': re.compile(r'^vscode-v(\d+(?:\.\d+)*)$')}
ASSETS = {'jetbrains': '.zip', 'vscode': '.vsix'}
LABELS = {'jetbrains': 'IntelliJ · Claude Chat', 'vscode': 'VS Code · Claude Chat'}
MAX_DOWNLOAD = 50 * 1024 * 1024
CACHE_SECONDS = 3600

_LATEST = {'at': 0.0, 'value': {}}


def version_key(text):
    return tuple(int(part) for part in re.findall(r'\d+', text or ''))


def latest():
    """{'jetbrains': {'version', 'url'}, 'vscode': {...}} from the releases of the plugin; {} when GitHub cannot be read."""
    if _LATEST['value'] and time.time() - _LATEST['at'] < CACHE_SECONDS:
        return _LATEST['value']
    try:
        req = urllib.request.Request(RELEASES, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'pr-watch'})
        with urllib.request.urlopen(req, timeout=15) as response:
            releases = json.loads(response.read().decode('utf-8', 'replace'))
    except (OSError, ValueError):
        return _LATEST['value']  # the previous answer, if any: a network failure is not news
    found = {}
    for release in releases:
        for kind, pattern in TAGS.items():
            match = pattern.match(release.get('tag_name', ''))
            asset = next((a for a in release.get('assets', []) if a['name'].endswith(ASSETS[kind])), None)
            if match and asset and (kind not in found or version_key(match.group(1)) > version_key(found[kind]['version'])):
                found[kind] = {'version': match.group(1), 'url': asset['browser_download_url']}
    _LATEST.update(at=time.time(), value=found)
    return found


def installed():
    """The plugins found on the machine: {'jetbrains': [(folder, version), ...], 'vscode': [(folder, version), ...]}."""
    jetbrains = []
    for folder in pweditors.jetbrains_plugin_dirs():
        jars = [m.group(1) for name in os.listdir(os.path.join(folder, 'lib')) if (m := re.match(r'^claude-chat-(\d[\d.]*)\.jar$', name))] if os.path.isdir(os.path.join(folder, 'lib')) else []
        if jars:
            jetbrains.append((folder, max(jars, key=version_key)))
    vscode = [(folder, os.path.basename(folder).rsplit('-', 1)[1]) for folder in pweditors.vscode_extension_dirs() if re.search(r'-\d[\d.]*$', folder)]
    return {'jetbrains': jetbrains, 'vscode': [max(vscode, key=lambda f: version_key(f[1]))] if vscode else []}


def status():
    """One line per plugin installed: {'id', 'label', 'installed', 'latest', 'outdated'}; 'latest' is None when unknown."""
    news, lines = latest(), []
    for kind, found in installed().items():
        if not found:
            continue
        oldest = min((version for _, version in found), key=version_key)
        newest = (news.get(kind) or {}).get('version')
        lines.append({'id': kind, 'label': LABELS[kind], 'installed': oldest, 'latest': newest, 'outdated': bool(newest) and version_key(oldest) < version_key(newest)})
    return lines


def _download(url, folder):
    if not url.startswith(f'https://github.com/{REPO}/releases/download/'):
        raise ValueError('adresse de téléchargement inattendue')
    target = os.path.join(folder, os.path.basename(url))
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'pr-watch'}), timeout=60) as response, open(target, 'wb') as out:
        size = 0
        while chunk := response.read(65536):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise ValueError('téléchargement trop gros')
            out.write(chunk)
    return target


def _unpack_jetbrains(archive, folder):
    """Unpacks the plugin zip into `folder`, refusing any path that would leave it; returns the unpacked `claude-chat` folder."""
    root = os.path.realpath(folder)
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not os.path.realpath(os.path.join(root, name)).startswith(root + os.sep) or not name.startswith('claude-chat/'):
                raise ValueError(f'contenu inattendu dans l’archive : {name}')
        bundle.extractall(root)
    return os.path.join(root, 'claude-chat')


def update(kind):
    """Updates the plugin of `kind` to the latest release. Returns (ok, message)."""
    news = latest().get(kind)
    if not news:
        return False, 'Impossible de lire les versions disponibles sur GitHub.'
    here = installed().get(kind) or []
    if not here:
        return False, 'Plugin non installé.'
    with tempfile.TemporaryDirectory() as work:
        try:
            archive = _download(news['url'], work)
            if kind == 'vscode':
                ok, output = pweditors.install_vscode_extension(archive)
                return ok, (f"Extension VS Code mise à jour en {news['version']} : rechargez la fenêtre de VS Code." if ok else f'Échec de `code --install-extension` : {output[-300:]}')
            fresh = _unpack_jetbrains(archive, os.path.join(work, 'unpacked'))
            for folder, _ in here:
                backup = folder + '.old'
                shutil.rmtree(backup, ignore_errors=True)
                os.replace(folder, backup)
                try:
                    shutil.copytree(fresh, folder)
                except OSError:
                    os.replace(backup, folder)  # put the working plugin back
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            return True, f"Plugin JetBrains mis à jour en {news['version']} dans {len(here)} IDE : redémarrez-les pour le charger."
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            return False, f'Mise à jour impossible : {error}'
