"""Runs the UAT scenarios against the local NextDLP stack and agent.

  python run.py --all
  python run.py --scenario 01-keyword-secrecy
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
from harness.scenario import Scenario


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="run every scenario")
    target.add_argument("--scenario", action="append", help="run one scenario (repeatable)")
    target.add_argument("--cleanup", action="store_true", help="only remove leftovers")
    parser.add_argument("--keep", action="store_true", help="skip cleanup after each scenario")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()

    settings = Settings()
    api = Api(settings.base_url, settings.admin_user, settings.admin_password)

    if args.cleanup:
        print("removed:", api.delete_uat_objects(settings.prefix) or "nothing")
        return 0

    folders = sorted(p for p in settings.scenarios_dir.iterdir() if (p / "scenario.yaml").exists())
    if args.scenario:
        folders = [p for p in folders if p.name in args.scenario]
        missing = set(args.scenario) - {p.name for p in folders}
        if missing:
            parser.error(f"unknown scenario(s): {', '.join(sorted(missing))}")

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=not args.headed)
        for folder in folders:
            def report(result):
                log = write_log(settings.reports_dir, result)
                print(log.read_text(encoding="utf-8"), end="", flush=True)

            print(f"running {folder.name} …", flush=True)
            result = Scenario(folder, settings, api, run_id).run(browser, args.keep, report)
            if not args.keep:
                print(append_cleanup(settings.reports_dir / f"{result.name}.log", result), end="", flush=True)
            results.append(result)
        browser.close()
    return 0 if all(r.passed and r.cleanup_ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
