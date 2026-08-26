import base64, glob, json, re, sys

# Titles for each dashboard page, in render order (must match snapshot_pdf.py PAGES).
titles = ['AiA Ops', 'CS & Finance', 'AiA Marketing Tracker', 'VA Ops', 'VA Finance', 'AiA Bot']
base = sys.argv[1]


def _num(p):
    m = re.search(r'_page-(\d+)\.jpg$', p)
    return int(m.group(1)) if m else 0


# Package EVERY page image pdftoppm produced (not a hardcoded count), in numeric
# order, so adding/removing a dashboard page never silently drops the last ones.
paths = sorted(glob.glob(f'{base}_page-*.jpg'), key=_num)

pages = []
for p in paths:
    i = _num(p)
    with open(p, 'rb') as f:
        data = base64.b64encode(f.read()).decode()
    pages.append({
        'cid': f'page{i}',
        'filename': f'page-{i}.jpg',
        'title': titles[i - 1] if 1 <= i <= len(titles) else f'Page {i}',
        'contentBytes': data,
    })

print(json.dumps(pages))
