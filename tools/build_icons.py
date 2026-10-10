"""Check runtime icons in memory; export only when explicitly requested."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.icons import asset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true', help='Explicitly save image assets to web/')
    args = parser.parse_args()
    names = ['icon.svg', 'favicon.ico', 'icon-maskable-512.png'] + [f'icon-{n}.png' for n in (16,32,180,192,512)]
    for name in names:
        content, _ = asset(name)
        if args.export:
            target = ROOT / 'web' / ('favicon.ico' if name == 'favicon.ico' else 'icons/' + name)
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(content)
    print('Icon assets exported.' if args.export else 'A icon assets validated in memory; no image files saved.')


if __name__ == '__main__':
    main()
