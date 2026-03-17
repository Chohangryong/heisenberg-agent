"""Inspect detail page sections and meta structure."""

from bs4 import BeautifulSoup

with open("/tmp/heisenberg_detail.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

# Detail: h1 parent structure
print("=== H1 parent chain ===")
h1 = soup.select_one("h1")
if h1:
    p = h1.parent
    while p and p.name != "body":
        print(f"  <{p.name} class={p.get('class', [])}>")
        p = p.parent

# Meta structure
print("\n=== .meta structure ===")
metas = soup.select(".meta")
for m in metas[:3]:
    parent = m.parent
    print(f"  <{m.name} class={m.get('class', [])}> text={m.get_text(strip=True)[:80]}")
    print(f"    parent: <{parent.name} class={parent.get('class', [])}>")
    # children
    for child in m.find_all(recursive=False):
        print(f"    child: <{child.name} class={child.get('class', [])}> {child.get_text(strip=True)[:40]}")

# single-content children detail
print("\n=== .single-content children (all content-blocks) ===")
sc = soup.select_one(".single-content")
if sc:
    for block in sc.select(".content-block"):
        classes = block.get("class", [])
        # Extract section type from class name
        section_type = [c for c in classes if c.startswith("content-") and c != "content-block"]
        # First text child or heading
        heading = block.select_one("h2, h3, .title, .heading")
        heading_text = heading.get_text(strip=True)[:40] if heading else ""
        first_text = block.get_text(strip=True)[:60]
        print(f"  classes={section_type}")
        print(f"    heading: {heading_text}")
        print(f"    text: {first_text[:50]}")
        print()

# Latest page: post-meta structure for author/category/date
print("=== LATEST PAGE: post-meta structure ===")
with open("/tmp/heisenberg_latest.html") as f:
    lsoup = BeautifulSoup(f.read(), "html.parser")

loop = lsoup.select_one("div.loop-list")
if loop:
    cards = loop.select("div.content")
    if cards:
        card = cards[0]
        print(f"  First card direct children:")
        for child in card.find_all(recursive=False):
            cls = child.get("class", [])
            text = child.get_text(strip=True)[:80]
            print(f"    <{child.name} class={cls}> {text[:60]}")

        print(f"\n  post-meta children:")
        pm = card.select_one(".post-meta")
        if pm:
            for child in pm.find_all(recursive=False):
                cls = child.get("class", [])
                text = child.get_text(strip=True)[:60]
                print(f"    <{child.name} class={cls}> {text}")
