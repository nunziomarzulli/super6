#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.superenalotto.it/archivio-estrazioni"
OUT = Path("data/draws-auto.json")

MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def to_iso(day: str, month: str, year: str) -> str:
    m = MONTHS[month.lower()]
    return f"{int(year):04d}-{m:02d}-{int(day):02d}"


def valid_number(n: int) -> bool:
    return 1 <= n <= 90


def fetch_with_requests() -> str | None:
    try:
        r = requests.get(
            URL,
            headers={
                "User-Agent": UA,
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=30,
        )
        if r.status_code == 200 and "Concorso" in r.text:
            return r.text
        print(f"requests: HTTP {r.status_code}; provo browser headless.")
    except Exception as e:
        print(f"requests fallito: {e}; provo browser headless.")
    return None


def fetch_with_browser() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="it-IT",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()
        if "Concorso" not in html:
            raise RuntimeError("La pagina ufficiale non contiene i dati attesi.")
        return html


def parse_table_rows(soup: BeautifulSoup) -> list[dict]:
    out = []
    date_rx = re.compile(
        r"(?:Concorso\s*)?N[º°o.]?\s*(\d+)\s*del\s*"
        r"(\d{1,2})\s+"
        r"(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)"
        r"\s+(\d{4})",
        re.I,
    )

    for tr in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.select("th,td")]
        if len(cells) < 4:
            continue

        m = date_rx.search(cells[0])
        if not m:
            continue

        nums = [int(x) for x in re.findall(r"\b\d{1,2}\b", cells[1])]
        nums = [x for x in nums if valid_number(x)]
        if len(nums) != 6 or len(set(nums)) != 6:
            continue

        jnums = [int(x) for x in re.findall(r"\b\d{1,2}\b", cells[2])]
        snums = [int(x) for x in re.findall(r"\b\d{1,2}\b", cells[3])]
        if not jnums or not snums:
            continue

        out.append(
            {
                "contest": str(int(m.group(1))),
                "date": to_iso(m.group(2), m.group(3), m.group(4)),
                "numbers": sorted(nums),
                "jolly": jnums[0],
                "superstar": snums[0],
            }
        )
    return out


def parse_text_fallback(soup: BeautifulSoup) -> list[dict]:
    text = soup.get_text(" ", strip=True)
    heading_rx = re.compile(
        r"(?:Concorso\s*)?N[º°o.]?\s*(\d+)\s*del\s*"
        r"(\d{1,2})\s+"
        r"(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)"
        r"\s+(\d{4})",
        re.I,
    )
    matches = list(heading_rx.finditer(text))
    out = []

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]

        j = re.search(r"\bJolly\b\s*([1-9]|[1-8]\d|90)\b", chunk, re.I)
        s = re.search(r"\bSuper\s*Star\b\s*([1-9]|[1-8]\d|90)\b", chunk, re.I)
        if not s:
            s = re.search(r"\bSuperStar\b\s*([1-9]|[1-8]\d|90)\b", chunk, re.I)
        if not j or not s:
            continue

        before_jolly = chunk[: j.start()]
        nums = [
            int(x)
            for x in re.findall(r"\b(?:[1-9]|[1-8]\d|90)\b", before_jolly)
        ]
        # Scarta eventuali numeri estranei e prende il primo blocco valido di 6 distinti.
        combo = None
        for k in range(0, max(0, len(nums) - 5)):
            cand = nums[k : k + 6]
            if len(cand) == 6 and len(set(cand)) == 6 and all(valid_number(x) for x in cand):
                combo = sorted(cand)
                break
        if not combo:
            continue

        out.append(
            {
                "contest": str(int(m.group(1))),
                "date": to_iso(m.group(2), m.group(3), m.group(4)),
                "numbers": combo,
                "jolly": int(j.group(1)),
                "superstar": int(s.group(1)),
            }
        )
    return out


def parse_draws(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = parse_table_rows(soup)
    if not rows:
        rows = parse_text_fallback(soup)

    dedup = {}
    for d in rows:
        if (
            len(d["numbers"]) == 6
            and len(set(d["numbers"])) == 6
            and all(valid_number(x) for x in d["numbers"])
            and valid_number(int(d["jolly"]))
            and valid_number(int(d["superstar"]))
        ):
            dedup[d["date"]] = d

    return sorted(dedup.values(), key=lambda x: x["date"], reverse=True)


def load_existing() -> dict:
    if not OUT.exists():
        return {"source": URL, "updatedAt": None, "draws": []}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"source": URL, "updatedAt": None, "draws": []}


def main() -> int:
    html = fetch_with_requests()
    if html is None:
        html = fetch_with_browser()

    fresh = parse_draws(html)
    if not fresh:
        print("ERRORE: nessuna estrazione riconosciuta nella pagina ufficiale.", file=sys.stderr)
        return 2

    current = load_existing()
    existing = {
        str(d.get("date")): d
        for d in current.get("draws", [])
        if d.get("date")
    }

    before = json.dumps(existing, sort_keys=True, ensure_ascii=False)

    for d in fresh:
        existing[d["date"]] = d

    merged = sorted(existing.values(), key=lambda x: x["date"], reverse=True)[:500]
    after_map = {d["date"]: d for d in merged}
    after = json.dumps(after_map, sort_keys=True, ensure_ascii=False)

    if before == after:
        print(f"Nessuna nuova estrazione. Ultima: {merged[0]['date']} concorso {merged[0]['contest']}")
        return 0

    payload = {
        "source": URL,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "draws": merged,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Aggiornato {OUT}: {len(merged)} estrazioni; "
        f"ultima {merged[0]['date']} concorso {merged[0]['contest']} "
        f"{merged[0]['numbers']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
