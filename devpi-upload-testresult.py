#
# Upload external tox JSON report to DevPI
# Author: Abel Cheung <abelcheung@gmail.com>
# Distributed under same license as DevPI (MIT)
#

import argparse
import json
import sys
from contextlib import closing
from typing import IO, TYPE_CHECKING

import devpi.main as _main
import devpi.test as _test
import more_itertools
import py
from devpi_common.url import URL

if TYPE_CHECKING:
    from devpi_common.viewhelp import ViewLink


def main():

    argparser = _main.MyArgumentParser(
        description='Upload external tox JSON result to DevPI.')
    argparser.add_argument("--index", default=None,
        help="index to get package from, defaults to current index. "
             "Either just the NAME, using the current user, USER/NAME using "
             "the current server or a full URL for another server.")
    argparser.add_argument("pkgspec", metavar="pkgspec", type=str,
        default=None, action="store",
        help="package specification in pip/setuptools requirement-syntax, "
             "e.g. 'pytest' or 'pytest==2.4.2'")
    argparser.add_argument(
        'json_result',
        metavar='json_result',
        nargs='+',
        type=argparse.FileType('r', encoding='utf-8'),
        help='JSON file produced by tox --result-json option',
    )
    _main.add_generic_options(argparser)

    args = argparser.parse_args()

    # Simulate command line client: devpi test "pkgspec"
    client_argv = (sys.argv[0], 'test', '-l', args.pkgspec)
    pm = _main.get_pluginmanager()
    hub_args = _main.parse_args(client_argv, pm)
    hub = _main.Hub(hub_args, pm=pm)

    with closing(hub):
        sdist = latest_sdist(hub)
        upload_result(hub, sdist, *args.json_result)


def latest_sdist(hub):
    current = hub.current
    args = hub.args
    index = args.index

    if index:
        if index.startswith(('http:', 'https:')):
            current = hub.current.switch_to_temporary(hub, index)
            index = None
        elif index.count("/") > 1:
            hub.fatal("index %r not of form URL, USER/NAME or NAME" % index)

    tmpdir = py.path.local.make_numbered_dir("devpi-test", keep=3)
    devindex = _test.DevIndex(hub, tmpdir, current)

    versioninfo = devindex.get_matching_versioninfo(args.pkgspec, index)
    if not versioninfo:
        hub.fatal("could not find/receive links for", args.pkgspec)
    links = versioninfo.get_links("releasefile")
    if not links:
        hub.fatal("could not find/receive links for", args.pkgspec)

    sdist_links, _ = _test.find_sdist_and_wheels(
        hub, links, universal_only=True)
    try:
        return more_itertools.one(sdist_links)
    except:
        raise RuntimeError('Found none or more than one sdist')


def upload_result(hub: _main.Hub, sdist: ViewLink, *report_fh: IO[str]):
    hub.info("Found sdist: " + sdist.href)
    url = URL(sdist.href)
    for idx, f in enumerate(report_fh, start=1):
        try:
            jsondata = json.load(f)
        except json.JSONDecodeError:
            hub.error(f'Report file at argument index {idx} is not JSON, skip processing')
            f.close()
            continue
        else:
            f.close()
            if 'toxversion' not in jsondata:
                hub.error(f'Report file at argument index {idx} '
                    'is not produced by tox, skip processing')
                continue
        hub.info(f'Attempting to post report file at argument index {idx}...')
        _test.post_tox_json_report(hub, url.url_nofrag, jsondata)

if __name__ == '__main__':
    main()
