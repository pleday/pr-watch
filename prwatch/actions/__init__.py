"""What a front end can ask of PR Watch: read the state, act on a watched pull request, change the options, read the journal.

Used by the web dashboard and the status icon. Every function returns plain data (dicts, lists, strings) that can be sent as
JSON; an action returns {'ok': bool, 'message': str, ...}. Nothing here draws anything.

This is the front door: the work is in `status` (what is shown), `forms`, `pr`, `sessions`, `tickets`, `adding`, `options`,
`common`, and `dispatch` (the table of actions).
"""
from .dispatch import perform  # noqa: F401
from .pr import focus, set_focus  # noqa: F401
from .status import journal, overview  # noqa: F401
