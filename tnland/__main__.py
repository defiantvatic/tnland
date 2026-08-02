"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import webbrowser


def build_parser() -> argparse.ArgumentParser:
    """Built separately from main() so the test suite can parse arguments
    without starting a web server. The no-subcommand path used to crash
    here and nothing caught it."""
    parser = argparse.ArgumentParser(
        prog="python -m tnland",
        description="Tennessee land research tool -- free public data only.",
    )
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="Start the map interface (default)")
    p_serve.add_argument("--port", type=int, default=8823)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--no-browser", action="store_true")

    p_doc = sub.add_parser("doctor", help="Check every data source is alive")
    p_doc.add_argument("--verbose", "-v", action="store_true")
    p_doc.add_argument("--lon", type=float,
                       help="Test a specific location instead of letting "
                            "doctor sample a parcel from the statewide layer")
    p_doc.add_argument("--lat", type=float)

    p_parcel = sub.add_parser("parcel", help="Print a report for one point")
    p_parcel.add_argument("lon", type=float)
    p_parcel.add_argument("lat", type=float)
    p_parcel.add_argument("--json", action="store_true")
    p_parcel.add_argument("--drivetimes", action="store_true",
                          help="include drive times (slower on a cold cache)")

    p_addr = sub.add_parser("address", help="Report for a street address")
    p_addr.add_argument("query", help='e.g. "2926 Bryant Ridge Rd, Baxter, TN"')
    p_addr.add_argument("--json", action="store_true")
    p_addr.add_argument("--drivetimes", action="store_true",
                        help="include drive times (slower on a cold cache)")
    p_addr.add_argument("--min-acres", type=float, default=None,
                        help='road searches ("0 Road Name") only list '
                             'parcels of at least this deeded acreage')

    p_cache = sub.add_parser("cache", help="Inspect or clear the local cache")
    p_cache.add_argument("action", choices=["stats", "clear"], default="stats",
                         nargs="?")

    # `python -m tnland` with no subcommand means "serve", but in that case
    # argparse never runs the serve subparser, so none of its defaults exist
    # on the namespace. Declaring them on the root parser guarantees they are
    # always present; the subparser still overrides them when it does run.
    parser.set_defaults(host="127.0.0.1", port=8823, no_browser=False,
                        verbose=False, lon=None, lat=None, action="stats",
                        json=False, query=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Narrate source activity ("FEMA flood: running", "Drive times:
    # searching: Hospital") in the terminal for every command, so the user
    # can watch the tool work instead of staring at a silent prompt.
    logging.basicConfig(format="  %(message)s")
    logging.getLogger("tnland").setLevel(logging.INFO)

    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "doctor":
        from .doctor import run

        return run(verbose=getattr(args, "verbose", False),
                   lon=getattr(args, "lon", None),
                   lat=getattr(args, "lat", None))

    if command == "cache":
        from . import cache

        if args.action == "clear":
            print(f"Cleared {cache.clear()} cached responses.")
        else:
            stats = cache.stats()
            print(f"{stats['entries']} entries, "
                  f"{stats['bytes'] / 1e6:.1f} MB at {stats['path']}")
        return 0

    if command == "address":
        from .analysis import address_report

        report = address_report(args.query, include=_cli_layers(args),
                                min_acres=getattr(args, "min_acres", None))
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_report(report)
        return 0 if report.get("found") else 1

    if command == "parcel":
        from .analysis import parcel_report

        report = parcel_report(args.lon, args.lat, include=_cli_layers(args))
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_report(report)
        return 0 if report.get("found") else 1

    # serve
    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    from . import build_info
    print(f"\n  TN Land Tool v{build_info()} running at {url}")
    print("  Data: TN Comptroller, county GIS, FEMA, USFWS, USGS, USDA NRCS")
    print("  Ctrl-C to stop\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("tnland.server:app", host=args.host, port=args.port,
                log_level="warning")
    return 0


def _cli_layers(args) -> set[str]:
    """Fast by default; --drivetimes opts into the slow layer."""
    layers = {"flood", "wetlands", "slope", "roads", "soils"}
    if getattr(args, "drivetimes", False):
        layers.add("drivetimes")
    return layers


def _print_report(report: dict) -> None:
    if report.get("kind") == "road" and report.get("found"):
        print(f"\n{report['message']}")
        print(f"{report.get('note','')}\n")
        print(f"{'ACRES':>8}  {'LAND USE':<24} {'OWNER':<30} PARCEL")
        for r in report["results"][:40]:
            acres = f"{r['acres']:.2f}" if r.get("acres") else "--"
            mark = "*" if r.get("is_raw_land") else " "
            print(f"{acres:>8}{mark} {(r.get('land_use') or '--')[:24]:<24} "
                  f"{(r.get('owner') or '--')[:30]:<30} {r.get('parcel_id') or ''}")
        if len(report["results"]) > 40:
            print(f"\n... and {len(report['results']) - 40} more")
        print("\n* = vacant, agricultural or timber\n")
        return
    if not report.get("found"):
        print(report.get("message", "Not found"))
        for c in report.get("candidates", [])[:10]:
            label = c.get("address") or c.get("owner") or ""
            coords = (f"  ({c['lat']:.5f}, {c['lon']:.5f})"
                      if c.get("lat") and c.get("lon") else "")
            print(f"  - {label}{coords}")
        return
    if report.get("matched_address"):
        print(f"\nMatched: {report['matched_address']}  "
              f"[{report.get('geocoder', '')}]")
    p = report["parcel"]
    print(f"\n{p.get('owner') or 'Unknown owner'}")
    print(f"{p.get('situs_address') or '(no address)'} -- "
          f"{p.get('county')} County")
    print(f"Parcel {p.get('parcel_id')}   "
          f"{p.get('deeded_acres') or p.get('gis_acres')} acres")
    if p.get("land_use"):
        print(f"Land use: {p['land_use']} (code {p.get('land_use_code')})")
    if p.get("appraisal"):
        print(f"Appraised: ${p['appraisal']:,.0f}"
              + (f"  (${p['appraised_per_acre']:,.0f}/acre)"
                 if p.get("appraised_per_acre") else ""))
    print()
    for flag in report.get("flags", []):
        mark = {"bad": "!!", "warn": " !", "ok": " +", "info": " ."}.get(
            flag["level"], "  ")
        print(f" {mark} {flag['text']}")
    dt = report.get("drivetimes", {})
    if not dt:
        print("\nDrive times: not checked (pass --drivetimes to include them)")
    if dt.get("available"):
        print("\nDrive times (free-flow, from parcel centroid):")
        for r in dt.get("results", []):
            target = (f"target {r['threshold_min']} min"
                      if r.get("threshold_min") else "info only")
            if r.get("found"):
                mark = "!!" if r.get("over") else "  "
                print(f" {mark} {r['label']:<20} {r['minutes']:>6.0f} min  "
                      f"{r['miles']:>5.1f} mi  {r['name']}  [{target}]")
            else:
                print(f"    {r['label']:<20}    -- "
                      f" {r.get('error') or r.get('note', '')}")
    if p.get("tpad_url"):
        print(f"\nTPAD: {p['tpad_url']}")
    print(f"\n{report.get('disclaimer', '')}\n")


if __name__ == "__main__":
    sys.exit(main())
