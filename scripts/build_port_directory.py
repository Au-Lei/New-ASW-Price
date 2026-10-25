"""One-off fixed-template conversion, not the future quotation upload feature."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.catalog_import import parse_template, SEED

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('template', type=Path)
    parser.add_argument('--output', type=Path, default=SEED)
    args = parser.parse_args()
    data = parse_template(args.template)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in data.items() if k != 'ports'}, ensure_ascii=False))
