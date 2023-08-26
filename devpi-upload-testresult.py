#
# Upload external tox JSON report to DevPI
# Author: Abel Cheung <abelcheung@gmail.com>
# Distributed under same license as DevPI (MIT)
# Inspired from https://stackoverflow.com/q/59518800/3830926
#
# For now, --debug, --index and -v options should be properly
# propagated to devpi internal, while -r/--dry-run are local arguments.
#

from __future__ import annotations

import argparse
import json
import os
import os.path
import sys
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator, List, cast

import devpi.main as _main
import devpi.test as _test
import more_itertools
import py
from devpi_common.url import URL

if TYPE_CHECKING:
    from devpi.use import Current
    from devpi_common.viewhelp import ViewLink


def main():
    argparser = argparse.ArgumentParser(
        description="Upload external tox JSON result to DevPI."
    )
    argparser.add_argument(
        "--index",
        help="index to get package from, defaults to current index. "
        "Either just the NAME, using the current user, USER/NAME using "
        "the current server or a full URL for another server.",
    )
    argparser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="recurse into subdir and search for all possible tox JSON files. "
        "If not specified, only check for immediate children of a folder.",
    )
    argparser.add_argument(
        "--dry-run",
        dest="simulate",
        action="store_true",
        help="Simulate process without actual upload of JSON data",
    )
    argparser.add_argument(
        "pkgspec",
        type=str,
        help="package specification in pip/setuptools requirement-syntax, "
        "e.g. 'pytest' or 'pytest==2.4.2'",
    )
    argparser.add_argument(
        "result_loc",
        metavar="json_or_dir",
        nargs="+",
        type=str,
        help="JSON file produced by tox --result-json option, "
        "or folder containing such files. Only files with .json "
        "extension are processed.",
    )
    _main.add_generic_options(argparser)
    args = argparser.parse_args()

    # --dry-run implies -v
    if args.simulate:
        args.verbose = (args.verbose or 0) + 1

    # Wrapper on top of devpi argument parsing mechanism
    # 1. Process and pick arguments which 'devpi test' subcommand can understand
    # 2. Simulate 'devpi test -l "pkgspec"' and append those arguments
    # 3. Devpi parse the arguments and produce namespace object
    # 4. Add our own attributes to this object
    client_argv = [argparser.prog, "test", '-l']
    if args.debug:
        client_argv.append('--debug')
    if args.verbose:
        client_argv.extend(['-v'] * args.verbose)
    if args.index:
        client_argv.extend(['--index', args.index])
    client_argv.append(args.pkgspec)

    pm = _main.get_pluginmanager()
    hub_args = _main.parse_args(client_argv, pm)
    hub_args.__dict__ = {**vars(args), **vars(hub_args)}
    hub = _main.Hub(hub_args, pm=pm)

    paths: List[Path] = []
    for loc in args.result_loc:
        p = Path(os.path.expandvars(loc)).expanduser()
        if p.exists():
            paths.append(p)
        else:
            hub.warn(f"Ignoring '{loc}', nonexistent file or folder")

    with closing(hub):
        sdist = latest_sdist(hub)
        url = URL(sdist.href)
        if hub.args.verbose:
            hub.info(f'Unique sdist found: {sdist}')
        for f in gen_json_list(hub, paths):
            upload_result(hub, url, f)


def iter_path(
    hub: _main.Hub,
    path: Path,
) -> Iterator[Path]:
    hub_args = hub.args
    if path.is_file():
        if is_valid_json(path):
            if hub_args.verbose:
                hub.info(f"Discovered report file '{path}'")
            yield path
        return
    if path.is_dir() and hub_args.recursive:
        for child in path.iterdir():
            yield from iter_path(hub, child)


def gen_json_list(
    hub: _main.Hub,
    paths: Iterable[Path],
) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            if is_valid_json(p):
                if hub.args.verbose:
                    hub.info(f"Discovered report file '{p}'")
                yield p
            else:
                hub.warn(f"Ignoring file '{p}', does not contain valid tox JSON result")
        elif p.is_dir():
            for child in p.iterdir():
                yield from iter_path(hub, child)


def is_valid_json(path: Path) -> bool:
    if path.suffix != '.json':
        return False
    try:
        content = path.read_text(encoding='utf8')
        jsondata = json.loads(content)
    except:
        return False
    else:
        # Only minimal validation here. Could do schema validation
        # if desired in future.
        return isinstance(jsondata, dict) and "toxversion" in jsondata


def latest_sdist(hub: _main.Hub) -> ViewLink:
    current = cast("Current", hub.current)
    args = hub.args
    index = args.index
    pkgspec = args.pkgspec

    if index:
        if index.startswith(("http:", "https:")):
            current = current.switch_to_temporary(hub, index)
            index = None
        elif index.count("/") > 1:
            hub.fatal(f'index {index!r} not of form URL, USER/NAME or NAME')

    devindex = _test.DevIndex(hub, py.path.local.mkdtemp(), current)
    versioninfo = devindex.get_matching_versioninfo(pkgspec, index)
    if not versioninfo:
        hub.fatal(f"No matching package version for '{pkgspec}'")
    links = versioninfo.get_links("releasefile")
    if not links:
        hub.fatal(f"No releasefile found for '{pkgspec}'")

    sdist_links, _ = _test.find_sdist_and_wheels(hub, links, universal_only=True)
    try:
        return more_itertools.one(sdist_links)
    except:
        msgs = [f"Multiple sdists found matching '{pkgspec}'"]
        msgs.extend(sdist_links)
        hub.fatal(os.linesep.join(msgs))


def upload_result(hub: _main.Hub, url: URL, path: Path):
    href = url.url_nofrag
    hub_args = hub.args
    # XXX Kinda inefficient to load json twice, but we are not dealing
    # with millions of files here, so...
    jsondata = json.loads(path.read_text())
    # devpi.test.post_tox_json_report is too verbose
    if hub_args.verbose:
        hub.info(f"Posting tox report '{path}' to sdist")
    if hub_args.simulate:
        return
    r = hub.http_api("post", href, kvdict=jsondata)
    if r.status_code == 200:
        if hub_args.verbose:
            hub.info(f"Successfully posted tox report '{path}'")
    else:
        hub.error(f"Could not post tox report '{path}' to '{href}'")


if __name__ == "__main__":
    main()
