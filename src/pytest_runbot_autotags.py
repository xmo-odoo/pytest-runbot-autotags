import inspect
import re
import urllib.error
import urllib.request
from typing import Callable

import pytest

SKIP_TAGGED = pytest.mark.skip(reason="tagged on runbot")

Tags = pytest.StashKey[list]()
TagPredicate = pytest.StashKey[Callable[[pytest.Item], None]]()
# TODO: use `TagsSelector` to parse this shit, add support for test parameters maybe
tag_re = re.compile(
    r"""
    # runbot automatically prepends `-` to disable
    -
    (:?/(?P<module>[\w/.]+))?
    (:?:(?P<class>\w+))?
    (:?\.(?P<method>\w+))?
""",
    re.VERBOSE,
)

pytest_plugins = ["pytest_odoo"]


def pytest_configure(config: pytest.Config):
    if config.getoption('--help'):
        return

    config.stash[Tags] = []
    try:
        # TODO: have runbot provide more useful cache headers so we don't need
        #       to read & parse the body if the version we have is still valid?
        r = urllib.request.urlopen("https://runbot.odoo.com/runbot/auto-tags", timeout=1)
    except (TimeoutError, urllib.error.URLError):
        predicates, tags = config.cache.get('autotags/auto-tags', ([], []))
    else:
        predicates = []
        tags = []
        # ignore match failure as technically the runbot *could* use an actual tag(ged)
        for m in filter(None, map(tag_re.fullmatch, r.read().decode().strip().split(','))):
            pred = []
            if n := m['method']:
                pred.append(f'fn.__name__ == {n!r}')
            if n := m['class']:
                n += '.'
                pred.append(f'fn.__qualname__.startswith({n!r})')
            if n := m['module']:
                if '/' in n:  # assume path
                    # `inspect.getfile` doesn't work correctly if the function
                    # is decorated, because it goes through the fucking code
                    # object's `co_filename` which can not be rewritten by
                    # `functools.wraps`, so we get the decorator's file
                    pred.append(f'getmodule(fn).__file__.endswith({n!r})')
                else:
                    n = f'odoo.addons.{n}.'
                    pred.append(f'fn.__module__.startswith({n!r})')
            if pred:
                predicates.append(" and ".join(pred))
                # fmt: off
                tags.append("".join(filter(None, [
                    m['module'] and (
                        f"odoo/addons/{m['module']}"
                        if '/' in m['module'] else
                        f"odoo/addons/{m['module']}/*"
                    ),
                    m['class'] and f"::{m['class']}",
                    m['method'] and f"::{m['method']}",
                ])))
                # fmt: on
        config.cache.set('autotags/auto-tags', (predicates, tags))
    if tags:
        config.stash[Tags] = tags
    if predicates:
        tagged = eval(
            "lambda fn: " + " or ".join(f"({p})" for p in predicates),
            {'__builtins__': {}, 'getmodule': inspect.getmodule},
            {},
        )
    else:

        def tagged(_):
            pass

    config.stash[TagPredicate] = tagged


# tryfist because pytest displays these in reverse loading order by default,
# so the last hook to run has its contents displayed first
@pytest.hookimpl(tryfirst=True)
def pytest_report_header(config: pytest.Config):
    tags = config.stash[Tags]
    return f"autotags ({len(tags)}): not ({' or '.join(tags)})"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    p = config.stash[TagPredicate]
    for item in items:
        # all items here seem to be function but might as well check
        if isinstance(item, pytest.Function) and p(item.function):
            item.add_marker(SKIP_TAGGED)
