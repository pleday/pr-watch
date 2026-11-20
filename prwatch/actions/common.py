"""What the actions share: the pull request of a request, the shape of an answer, the functions a declined permission hides."""

from prwatch.core import config as pwconfig
from prwatch.core import permissions as pwperm
from prwatch.core import store as pwstore

def _entry(repo, pr):
    for entry in pwstore.load_registry():
        if entry['repo'] == repo and entry['pr'] == pr:
            return entry
    return None


def _result(ok, message, **extra):
    return {'ok': ok, 'message': message, **extra}


def _hidden(feature):
    """A message when the user declined the permission `feature` needs (the function is hidden), else None."""
    if feature in pwperm.hidden_features():
        return _result(False, f"Fonction masquée : l’autorisation nécessaire pour {pwperm.FEATURE_LABELS[feature]} a été refusée (Options, Autorisations).")
    return None


def _not_mine(pr):
    """A refusal when the pull request is not the user's (only one's own pull requests can be added), else None."""
    mine = pwperm.is_mine(pr, pwconfig.config()['jiraSite'])
    if mine is None:
        return _result(False, "Votre compte n’est pas connu de PR Watch, donc impossible de vérifier que cette PR est la vôtre : voir Options, Autorisations.")
    if not mine:
        return _result(False, f"Cette PR est de {(pr.get('author') or {}).get('display_name', 'quelqu’un d’autre')} : seules vos propres pull requests peuvent être ajoutées.")
    return None
