"""Inspect detail page HTML for selectors."""

from bs4 import BeautifulSoup

with open("/tmp/heisenberg_detail.html") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

# Title
print("=== TITLE ===")
for sel in ["h1", "h1.title", "h1.entry-title", ".article-title", ".post-title"]:
    matches = soup.select(sel)
    if matches:
        print(f"  {sel} → {matches[0].get_text(strip=True)[:80]}")
        print(f"    class={matches[0].get('class', [])}")

# Author, category, date — look near the title
print("\n=== POST META ===")
for sel in [".post-meta", ".meta", ".entry-meta", ".article-meta",
            ".author", ".category", "time", ".date"]:
    matches = soup.select(sel)
    if matches:
        for m in matches[:2]:
            text = m.get_text(strip=True)[:100]
            print(f"  {sel} class={m.get('class', [])} → {text}")

# Content area
print("\n=== CONTENT AREA ===")
for sel in [".article-content", ".entry-content", ".post-content",
            ".content", "article .content", ".single-content"]:
    matches = soup.select(sel)
    if matches:
        print(f"  {sel} → {len(matches)} match(es), class={matches[0].get('class', [])}")
        # Show direct children structure
        children = matches[0].find_all(recursive=False)
        for c in children[:8]:
            cls = c.get("class", [])
            text = c.get_text(strip=True)[:60]
            print(f"    <{c.name} class={cls}> {text[:50]}")

# Sections — look for section-like divs
print("\n=== SECTION-LIKE DIVS ===")
for sel in [".one-minute-summary", ".main-body", ".researcher-opinion",
            ".researcher-profile", ".membership-gate", ".qa-section",
            ".coffeechat-section",
            "[class*=summary]", "[class*=opinion]", "[class*=body]",
            "[class*=section]", "[class*=gate]", "[class*=profile]"]:
    matches = soup.select(sel)
    if matches:
        for m in matches[:2]:
            text = m.get_text(strip=True)[:60]
            print(f"  {sel} class={m.get('class', [])} → {text[:50]}")

# H2/H3 headings inside content (section markers)
print("\n=== HEADINGS IN CONTENT ===")
content = soup.select_one(".entry-content") or soup.select_one(".content") or soup.select_one("article")
if content:
    for h in content.select("h2, h3"):
        cls = h.get("class", [])
        text = h.get_text(strip=True)[:80]
        print(f"  <{h.name} class={cls}> {text}")
