"""Runs the UAT suites against the local NextDLP stack and agent.

  python run.py --all
  python run.py --suite classification
  python run.py --suite classification --case 02-multi-domain
  python run.py --all --sequential  # drop one case at a time instead of all at once
  python run.py --all --keep        # leave what the run created, for debugging
  python run.py --all --headed      # watch the browser
  python run.py --cleanup           # remove leftovers of an interrupted run
"""
import argparse
import datetime
import sys

from playwright.sync_api import sync_playwright

from harness.api import Api
from harness.config import Settings
from harness.report import append_cleanup, write_log
from harness.smb import remove_leftovers as remove_smb_leftovers
from harness.suite import Suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="run every suite")
    target.add_argument("--suite", action="append", help="run one suite (repeatable)")
    target.add_argument("--cleanup", action="store_true", help="only remove leftovers")
    parser.add_argument("--case", action="append", help="with one --suite: run only this case (repeatable)")
    parser.add_argument("--sequential", action="store_true", help="drop one case at a time")
    parser.add_argument("--keep", action="store_true", help="skip cleanup")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()
    if args.case and (not args.suite or len(args.suite) != 1):
        parser.error("--case needs exactly one --suite")

    settings = Settings()
    api = Api(settings.base_url, settings.admin_user, settings.admin_password)

    if args.cleanup:
        removed = api.delete_uat_objects(settings.prefix) + remove_smb_leftovers()
        print("removed:", removed or "nothing")
        return 0

    folders = sorted(p for p in settings.suites_dir.iterdir() if (p / "setup.yaml").exists())
    if args.suite:
        folders = [p for p in folders if p.name in args.suite]
        missing = set(args.suite) - {p.name for p in folders}
        if missing:
            parser.error(f"unknown suite(s): {', '.join(sorted(missing))}")

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=not args.headed)
        for folder in folders:
            def report(result):
                log = write_log(settings.reports_dir, result)
                print(log.read_text(encoding="utf-8"), end="", flush=True)

            try:
                suite = Suite(folder, settings, api, run_id, args.case)
            except ValueError as error:
                parser.error(str(error))
            print(f"running {suite.name} ({len(suite.cases)} case(s)) …", flush=True)
            result = suite.run(browser, args.keep, args.sequential, report)
            if not args.keep:
                print(append_cleanup(settings.reports_dir / f"{result.name}.log", result), end="", flush=True)
            results.append(result)
        browser.close()
    return 0 if all(r.passed and r.cleanup_ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
