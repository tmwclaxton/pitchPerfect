# app/main.py — Discover autoswipe entrypoint (see also autoswipe.py).

"""
Examples:
  python setup_autoswipe.py --preset asian_baddies
  python main.py --setup
  python main.py --setup --preset asian_baddies
  python main.py
  python main.py --max-swipes 5 --no-paste
  python autoswipe.py --show-settings
"""

from autoswipe import main


if __name__ == "__main__":
    main()
