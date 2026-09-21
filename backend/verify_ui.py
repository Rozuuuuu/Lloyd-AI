"""Static verification of mobile.html: every DOM reference must resolve.

A single typo'd getElementById returns null, the first property access throws,
and the whole inline script dies - taking voice, chat and settings with it.
There is no browser here to catch that, so it is checked statically.
"""
import io
import re
import sys

p = r"C:\Users\Home\Desktop\AI\Jarvis AI\backend\mobile.html"
t = io.open(p, encoding="utf-8").read()

ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', t))
fails = []
checks = []


def check(label, ok, detail=""):
    checks.append((label, ok, detail))
    if not ok:
        fails.append(label + (" :: " + detail if detail else ""))


# 1. getElementById targets must exist
for ref in sorted(set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", t))):
    check("getElementById('%s')" % ref, ref in ids,
          "no element with that id" if ref not in ids else "")

# 2. CSS rules referencing ids must point at real ids
css = t[t.index("<style>"):t.index("</style>")]
for ref in sorted(set(re.findall(r"#([A-Za-z][A-Za-z0-9_-]*)\s*(?:[,{:\[])", css))):
    if re.fullmatch(r"[0-9a-fA-F]{3,8}", ref):
        continue  # hex colour, not a selector
    check("css #%s" % ref, ref in ids, "styled id not in markup")

# 3. querySelectorAll attribute hooks must match real attributes
for sel, attr in [("data-layer", 'data-layer="'),
                  ("data-font-opt", 'data-font-opt="'),
                  ("data-fs-opt", 'data-fs-opt="')]:
    n_js = len(re.findall(r"querySelectorAll\([^)]*%s" % sel, t))
    n_html = t.count(attr)
    check("%s: %d selector(s) -> %d element(s)" % (sel, n_js, n_html),
          n_js > 0 and n_html > 0)

# 4. the layer ids the CSS hides must be the ones the markup defines
for layer_id in ["chromeHeader", "stateBlock", "chatSection", "micMeters", "micBtn"]:
    check("layer target #%s" % layer_id, layer_id in ids)

# 5. gearBtn must NOT be inside any hidden layer, or core-only strands the user
gear_i = t.index('id="gearBtn"')
for layer_id in ["chromeHeader", "stateBlock", "chatSection", "micMeters"]:
    li = t.index('id="%s"' % layer_id)
    # crude but sufficient: the gear block is emitted before the header
    check("gearBtn outside #%s" % layer_id, gear_i < li,
          "gear appears after the layer opens")

# 6. body data-attributes written by JS must be the ones the CSS reads
js_attrs = set(re.findall(r"appBody\.dataset\.([A-Za-z]+)\s*=", t))
css_attrs = set(re.findall(r'body\[data-([a-z]+)=', css))
check("CSS reads only attributes JS writes",
      css_attrs <= js_attrs | {"state"},
      "css-only: %s" % sorted(css_attrs - js_attrs - {"state"}))
check("JS writes only attributes CSS reads",
      js_attrs <= css_attrs | {"state", "sheet"},
      "js-only: %s" % sorted(js_attrs - css_attrs - {"state", "sheet"}))

# 7. the old duplicated send path must be gone
check("voice no longer inlines its own ws.send",
      t.count("type:'user_message'") + t.count("type: 'user_message'") == 1,
      "found %d send sites; should be exactly 1 (sendMessage)"
      % (t.count("type:'user_message'") + t.count("type: 'user_message'")))

# 8. localStorage must always be guarded
for m in re.finditer(r"localStorage\.(getItem|setItem)", t):
    window = t[max(0, m.start() - 260):m.start()]
    check("localStorage %s guarded by try" % m.group(1), "try {" in window)

print("mobile.html static verification")
print("-" * 66)
for label, ok, detail in checks:
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", label,
                         "  <- " + detail if detail and not ok else ""))
print("-" * 66)
print("%d checks, %d failed" % (len(checks), len(fails)))
sys.exit(1 if fails else 0)
