"""Build a ZIP from an explicit release manifest. Credentials and local state are excluded."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).parent
MANIFEST = """
README.md VALIDATION.md DEPLOY_AR.md render.yaml .python-version .gitignore Start_BuyWise.cmd requirements.txt requirements-index.txt
buywise_api.py buywise_db.py buywise_hosting.py buywise_discover.py buywise_export.py buywise_image_index.py
buywise_ir_eval.py buywise_plus.py buywise_probe.py buywise_refresh_db.py buywise_retail.py
buywise_retrieval.py buywise_ui.html evaluation_template.html Judge_Rugs.html restore_demo.py
Judge_Rugs_baseline.html BuyWise_IR_results_baseline.md rug_patterns.json shop_settings.json Start_BuyWise_Online.cmd Remove_Old_Headphones.cmd remove_old_headphones.py rug_catalog.json catalog_sources.csv package_release.py tests/test_buywise.py
""".split()


def package(target, photos=True):
    paths = [ROOT / name for name in MANIFEST]
    if photos:
        paths += sorted((ROOT / "photos").glob("*.jpg"))
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    with ZipFile(target, "x", ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, "BuyWise/" + path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--links-only", action="store_true", help="Omit local copies of retailer photos"
    )
    args = parser.parse_args()
    package(args.output, not args.links_only)
