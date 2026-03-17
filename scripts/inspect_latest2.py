"""Deeper inspection of latest page structure."""

from bs4 import BeautifulSoup

with open("/tmp/heisenberg_latest.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

# Find all <a> tags that link to articles (heisenberg.kr/latest/*)
print("=== Links to articles ===")
article_links = soup.select('a[href*="/latest/"]')
for a in article_links[:5]:
    href = a.get("href", "")
    cls = a.get("class", [])
    parent = a.parent
    parent_cls = parent.get("class", []) if parent else []
    grandparent = parent.parent if parent else None
    gp_cls = grandparent.get("class", []) if grandparent else []
    text = a.get_text(strip=True)[:60]
    print(f"  <a class={cls} href={href}> {text}")
    print(f"    parent: <{parent.name} class={parent_cls}>")
    print(f"    grandparent: <{grandparent.name if grandparent else '?'} class={gp_cls}>")
    print()

# Find the main content container
print("=== Main content area ===")
for sel in [".latest-posts", ".posts-list", ".post-list", "main", ".main-content",
            "#content", ".content-area", ".posts", ".entries"]:
    matches = soup.select(sel)
    if matches:
        for m in matches[:1]:
            children_classes = set()
            for child in m.find_all(recursive=False):
                children_classes.add(f"<{child.name} class={child.get('class', [])}>")
            print(f"{sel} → direct children:")
            for c in list(children_classes)[:10]:
                print(f"  {c}")
        print()

# Try to find repeated article-like structures
print("=== Repeated patterns (likely article cards) ===")
# Find elements that contain both a title link and a date
all_divs = soup.find_all(["div", "article", "li", "section"])
patterns = {}
for div in all_divs:
    cls = tuple(div.get("class", []))
    if cls and len(div.select('a[href*="/latest/"]')) >= 1:
        key = " ".join(cls)
        patterns[key] = patterns.get(key, 0) + 1

for cls_name, count in sorted(patterns.items(), key=lambda x: -x[1]):
    if count >= 3:
        print(f"  .{cls_name.replace(' ', '.')} → {count} occurrences")
