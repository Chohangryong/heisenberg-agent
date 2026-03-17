"""Find actual article link patterns on latest page."""

from bs4 import BeautifulSoup

with open("/tmp/heisenberg_latest.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

# Find all <a> tags with href containing heisenberg.kr but not /latest/
print("=== All unique href patterns (non-nav) ===")
seen = set()
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "heisenberg.kr" not in href and not href.startswith("/"):
        continue
    if any(x in href for x in ["/wp-content/", "/wp-admin/", "#", "javascript:"]):
        continue
    text = a.get_text(strip=True)[:60]
    if href not in seen and text and len(text) > 10:
        seen.add(href)
        parent = a.parent
        pcls = parent.get("class", []) if parent else []
        print(f"  {href}")
        print(f"    text: {text}")
        print(f"    <a class={a.get('class', [])}> parent=<{parent.name if parent else '?'} class={pcls}>")
        print()
        if len(seen) > 20:
            break

# Also look for the first article title from screenshot
print("=== Search for 'GTC2026' text ===")
for el in soup.find_all(string=lambda t: t and "GTC2026" in t):
    p = el.parent
    while p:
        if p.get("class"):
            print(f"  <{p.name} class={p.get('class', [])}>")
        p = p.parent
    print()
