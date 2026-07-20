#!/usr/bin/env python3
"""
Configure Discover autoswipe filters (CLI and/or interactive).

Examples:
  python setup_autoswipe.py --show
  python setup_autoswipe.py --preset asian_baddies
  python setup_autoswipe.py --interactive
  python setup_autoswipe.py --min-composite 6 --ethnicity "East/Southeast Asian"
  python setup_autoswipe.py --list-presets
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from autoswipe_config import (
    AutoswipeSettings,
    PRESETS,
    apply_preset,
    format_settings,
    list_presets,
    load_settings,
    save_settings,
    settings_field_names,
)


def _prompt_value(label: str, current, cast):
    raw = input(f"{label} [{current}]: ").strip()
    if not raw:
        return current
    if cast is bool:
        return raw.lower() in {"1", "true", "yes", "y"}
    return cast(raw)


def run_interactive(base: AutoswipeSettings) -> AutoswipeSettings:
    print("Autoswipe setup (press Enter to keep current value)\n")
    print("Available presets:", ", ".join(list_presets()))
    preset = input(f"Apply preset name (or blank to edit fields) [{base.preset}]: ").strip()
    if preset:
        try:
            return apply_preset(preset)
        except ValueError as exc:
            print(exc)
            sys.exit(1)

    data = base.to_dict()
    data["min_composite"] = _prompt_value(
        "min_composite (like if composite >= this)", data["min_composite"], float
    )
    data["min_attractiveness"] = _prompt_value(
        "min_attractiveness floor", data["min_attractiveness"], float
    )
    data["min_slimness"] = _prompt_value(
        "min_slimness floor", data["min_slimness"], float
    )
    data["min_quirkiness"] = _prompt_value(
        "min_quirkiness floor", data["min_quirkiness"], float
    )
    data["min_ethnicity_fit"] = _prompt_value(
        "min_ethnicity_fit floor", data["min_ethnicity_fit"], float
    )
    data["weight_attractiveness"] = _prompt_value(
        "weight_attractiveness", data["weight_attractiveness"], float
    )
    data["weight_slimness"] = _prompt_value(
        "weight_slimness", data["weight_slimness"], float
    )
    data["weight_quirkiness"] = _prompt_value(
        "weight_quirkiness", data["weight_quirkiness"], float
    )
    data["weight_ethnicity_fit"] = _prompt_value(
        "weight_ethnicity_fit", data["weight_ethnicity_fit"], float
    )
    data["ethnicity_preference"] = _prompt_value(
        "ethnicity_preference (empty = none)",
        data["ethnicity_preference"],
        str,
    )
    data["profile_image_count"] = _prompt_value(
        "profile_image_count (vision photos)", data["profile_image_count"], int
    )
    data["max_swipes"] = _prompt_value("max_swipes per run", data["max_swipes"], int)
    data["paste_comment"] = _prompt_value(
        "paste_comment on like (true/false)", data["paste_comment"], bool
    )
    data["preset"] = "custom"
    settings = AutoswipeSettings(**{k: data[k] for k in settings_field_names()})
    return save_settings(settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure Discover autoswipe filters and scoring."
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print current saved settings and exit.",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="List named presets.",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        help=f"Apply a named preset ({', '.join(list_presets())}).",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive prompts for each filter.",
    )
    parser.add_argument("--min-composite", type=float, default=None)
    parser.add_argument("--min-attractiveness", type=float, default=None)
    parser.add_argument("--min-slimness", type=float, default=None)
    parser.add_argument("--min-quirkiness", type=float, default=None)
    parser.add_argument("--min-ethnicity-fit", type=float, default=None)
    parser.add_argument("--weight-attractiveness", type=float, default=None)
    parser.add_argument("--weight-slimness", type=float, default=None)
    parser.add_argument("--weight-quirkiness", type=float, default=None)
    parser.add_argument("--weight-ethnicity-fit", type=float, default=None)
    parser.add_argument(
        "--ethnicity",
        type=str,
        default=None,
        help='Ethnicity preference for vision scoring, e.g. "East/Southeast Asian".',
    )
    parser.add_argument("--image-count", type=int, default=None)
    parser.add_argument("--max-swipes", type=int, default=None)
    parser.add_argument(
        "--paste-comment",
        dest="paste_comment",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-paste-comment",
        dest="paste_comment",
        action="store_false",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        for name in list_presets():
            preset = PRESETS[name]
            print(
                f"{name}: min_composite={preset['min_composite']}, "
                f"ethnicity={preset.get('ethnicity_preference') or '(none)'}"
            )
        return 0

    if args.show and not any(
        [
            args.preset,
            args.interactive,
            args.min_composite is not None,
            args.min_attractiveness is not None,
            args.min_slimness is not None,
            args.min_quirkiness is not None,
            args.min_ethnicity_fit is not None,
            args.weight_attractiveness is not None,
            args.weight_slimness is not None,
            args.weight_quirkiness is not None,
            args.weight_ethnicity_fit is not None,
            args.ethnicity is not None,
            args.image_count is not None,
            args.max_swipes is not None,
            args.paste_comment is not None,
        ]
    ):
        print(format_settings(load_settings()))
        return 0

    if args.interactive:
        settings = run_interactive(load_settings())
        print("\nSaved:\n" + format_settings(settings))
        return 0

    if args.preset:
        overrides = _cli_overrides(args)
        settings = apply_preset(args.preset, overrides=overrides or None)
        print(f"Applied preset '{args.preset}'.\n")
        print(format_settings(settings))
        _print_hinge_note(settings)
        return 0

    overrides = _cli_overrides(args)
    if not overrides and not args.show:
        parser.print_help()
        print("\nTip: python setup_autoswipe.py --preset asian_baddies")
        return 1

    settings = load_settings()
    data = settings.to_dict()
    data.update(overrides)
    if overrides:
        data["preset"] = "custom" if settings.preset != "custom" else settings.preset
    settings = AutoswipeSettings(**{k: data[k] for k in settings_field_names()})
    save_settings(settings)
    print("Saved:\n" + format_settings(settings))
    _print_hinge_note(settings)
    return 0


def _cli_overrides(args) -> dict:
    mapping = {
        "min_composite": args.min_composite,
        "min_attractiveness": args.min_attractiveness,
        "min_slimness": args.min_slimness,
        "min_quirkiness": args.min_quirkiness,
        "min_ethnicity_fit": args.min_ethnicity_fit,
        "weight_attractiveness": args.weight_attractiveness,
        "weight_slimness": args.weight_slimness,
        "weight_quirkiness": args.weight_quirkiness,
        "weight_ethnicity_fit": args.weight_ethnicity_fit,
        "ethnicity_preference": args.ethnicity,
        "profile_image_count": args.image_count,
        "max_swipes": args.max_swipes,
        "paste_comment": args.paste_comment,
    }
    return {k: v for k, v in mapping.items() if v is not None}


def _print_hinge_note(settings: AutoswipeSettings) -> None:
    print("\nHinge Filters:")
    print(f"  {settings.hinge_filters_note}")
    if settings.ethnicity_preference:
        print(
            f"  Vision preference active: {settings.ethnicity_preference!r} "
            "(set Discover ethnicity/race filters in the Hinge app once if available)."
        )


if __name__ == "__main__":
    raise SystemExit(main())
