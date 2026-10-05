"""Archive the official pre-development 2019 Q3 industry table and release page."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
PAGE_URL = "https://www.csrc.gov.cn/csrc/c100103/c1451997/content.shtml"
OUTPUT = ROOT / "research_outputs/csrc_industry_2019q3_raw_v1"
VERSION = "csrc-official-industry-2019q3-source-v1"


def publication_and_attachment(html: str, page_url: str = PAGE_URL) -> tuple[str, str]:
    if "2019年3季度上市公司行业分类结果" not in html:
        raise ValueError("official release title differs")
    dates = re.findall(r'日期\s*[:：]\s*(2019-\d{2}-\d{2})', html)
    if len(set(dates)) != 1 or dates[0] != "2019-10-28":
        raise ValueError("official page publication date is missing or differs")
    links = re.findall(r'''href\s*=\s*["']([^"']+\.pdf)["']''', html, re.IGNORECASE)
    links = sorted({urljoin(page_url, link) for link in links})
    if len(links) != 1:
        raise ValueError("release page must link exactly one industry PDF")
    parsed = urlsplit(links[0])
    if (parsed.scheme != "https" or parsed.netloc != "www.csrc.gov.cn"
            or not parsed.path.startswith("/csrc/c100103/c1451997/1451997/files/")):
        raise ValueError("industry attachment is outside the verified official publication")
    return dates[0], links[0]


def fetch(output: Path = OUTPUT) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or output.exists():
        raise ValueError("official industry source requires a fresh research_outputs directory")
    captured = []
    for name, url in (("release_page.html", PAGE_URL),):
        with urlopen(Request(url, headers={"User-Agent": "MarketMirror-Research/1.0"}), timeout=25) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000 or response.geturl() != url:
                raise ValueError("release response size or redirected identity differs")
            captured.append((name, body, url, response.headers.get("Content-Type")))
    publication_date, pdf_url = publication_and_attachment(captured[0][1].decode("utf-8"))
    with urlopen(Request(pdf_url, headers={"User-Agent": "MarketMirror-Research/1.0"}), timeout=25) as response:
        body = response.read(20_000_001)
        if len(body) > 20_000_000 or not body.startswith(b"%PDF-") or response.geturl() != pdf_url:
            raise ValueError("industry attachment is not the expected bounded PDF response")
        captured.append(("industry_2019q3.pdf", body, pdf_url, response.headers.get("Content-Type")))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="csrc-industry-source-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        files = {}
        for name, body, url, content_type in captured:
            path = stage / name
            path.write_bytes(body)
            files[name] = {"sha256": file_sha256(path), "bytes": len(body), "url": url, "content_type": content_type}
        manifest = {"pipeline_version": VERSION, "publisher": "中国证券监督管理委员会",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(), "publication_date": publication_date,
                    "classification_period": "2019Q3", "available_at_proxy": "2019-10-29T00:00:00+08:00",
                    "date_basis": "official_page_date_next_day_conservative_proxy",
                    "artifacts": files, "code_sha256": file_sha256(Path(__file__)),
                    "limitations": ["Official historical version retrieved now, not an independently preserved 2019 point-in-time snapshot.",
                                    "Page publication date does not establish intraday first-publication time or original attachment bytes.",
                                    "Industry membership does not establish a policy impact direction, size or investment signal."]}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stage.replace(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    manifest = fetch(args.output_dir)
    print(json.dumps({"publication_date": manifest["publication_date"], "available_at_proxy": manifest["available_at_proxy"],
                      "files": list(manifest["artifacts"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
