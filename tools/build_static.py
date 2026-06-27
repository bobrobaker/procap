"""Render the ProCap web demo to a static site for GitHub Pages.

The live demo (procap.webdemo) is a dynamic stdlib http.server: it serves each run's
HTML from `render_page()` and streams keyframe PNGs from `/img?...`. GitHub Pages can
only host static files, so this build calls the *same* render path once per run and
rewrites its two dynamic URL shapes into relative files:

    /img?run=<R>&file=<F>   ->  assets/<R>/<F>        (PNGs copied alongside)
    href="/?run=<R>"        ->  href="<R>.html"        (one page per run)

The default run is also written as index.html. The "Run your own recording" upload
panel is stripped — it needs the server-side pipeline, which Pages can't run. Everything
else (the retag / annotate / confirm-gap editing and print-to-PDF) is client-side JS and
survives the export unchanged.

Usage:
    python tools/build_static.py --runs runs --out site
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

# Import the live demo's render path so the static site is byte-for-byte the same markup.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from procap.webdemo import _discover_runs, _default_active, render_page  # noqa: E402

# The two dynamic URL shapes emitted in served HTML (see webdemo._img_src and the chooser).
_IMG_RE = re.compile(r'/img\?run=([^&"]+)&file=([^"&]+)')
_RUNLINK_RE = re.compile(r'href="/\?run=([^"]+)"')
# The upload panel needs the server-side pipeline; drop it from the static build.
_UPLOAD_RE = re.compile(r'<section class="upload no-print">.*?</section>', re.DOTALL)


def _staticize(html_doc: str) -> str:
    """Rewrite a rendered page's dynamic URLs to relative paths and strip the upload panel."""
    html_doc = _UPLOAD_RE.sub("", html_doc)
    html_doc = _IMG_RE.sub(
        lambda m: f"assets/{unquote(m.group(1))}/{unquote(m.group(2))}", html_doc)
    html_doc = _RUNLINK_RE.sub(lambda m: f'href="{unquote(m.group(1))}.html"', html_doc)
    return html_doc


def build(runs_base: Path, out: Path) -> int:
    runs = _discover_runs(runs_base)
    if not runs:
        raise SystemExit(f"no runs found under {runs_base}/ — generate one with `procap run`")

    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)

    default = _default_active(runs)
    for active in runs:
        page = _staticize(render_page(runs, active))
        (out / f"{active.name}.html").write_text(page, encoding="utf-8")
        if active == default:
            (out / "index.html").write_text(page, encoding="utf-8")

        # Copy the keyframe PNGs the page references into assets/<run>/.
        src_kf = active / "keyframes"
        if src_kf.is_dir():
            shutil.copytree(src_kf, out / "assets" / active.name)

    # A .nojekyll file tells Pages to serve files verbatim (no Jekyll processing).
    (out / ".nojekyll").write_text("")

    print(f"built {len(runs)} run(s) -> {out}/  (default: {default.name})")
    for p in runs:
        print(f"  · {p.name}.html")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default="runs", help="base dir holding run dirs (default runs/)")
    ap.add_argument("--out", default="site", help="output dir for the static site (default site/)")
    args = ap.parse_args(argv)
    return build(Path(args.runs), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
