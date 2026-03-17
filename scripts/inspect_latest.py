"""Inspect latest page HTML for article card selectors."""

from bs4 import BeautifulSoup

with open("/tmp/heisenberg_latest.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

selectors_to_try = [
    "article.post-card",
    "div.post-card",
    ".post-card",
    "article",
    ".card",
    "[class*=post]",
    "[class*=article]",
    "[class*=card]",
    ".latest",
    "a[class*=post]",
    "div[class*=post]",
]

for sel in selectors_to_try:
    matches = soup.select(sel)
    if matches:
        print(f"{sel} → {len(matches)} matches")
        for m in matches[:2]:
            tag = m.name
            cls = m.get("class", [])
            href = m.get("href", "")
            text = m.get_text(strip=True)[:80]
            print(f"  <{tag} class={cls} href={href}> {text[:60]}")
        if len(matches) > 2:
            print(f"  ... and {len(matches) - 2} more")
        print()
