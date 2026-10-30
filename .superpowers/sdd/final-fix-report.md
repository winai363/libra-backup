# Calm Liquid Reader final-review fixes

Status: complete in the isolated worktree. No production changes or deployment.

## Findings and cause

1. Language of parts: the templates used content language as the inherited language of fallback English interface copy. Article editorial prose was fixed English under Spanish/French roots, and French index cards mixed English actions/counts with French content without overrides. The monolithic card `aria-label` also merged the two languages into a single attribute.
2. Topic counts: the index always selected the singular `tema` / `topic`, regardless of the inventory count.

## Fix

- The index document language now matches its supported interface language: Spanish for an entirely Spanish inventory, English otherwise. Each topic card retains its own content language.
- Topic and article documents retain their content language. Explicit UI-language annotations cover fallback navigation and its accessible label, skip links, section headings, book labels, cover alt text, topic CTAs, and topic editorial notes. Article guide-title links explicitly keep the topic language when their enclosing navigation uses another language.
- The existing article editorial text is unchanged and explicitly marked `lang="en"`. Article title, summary, body, book copy, and custom CTA text retain their content language. The disclosure label follows the UI language while its actual Spanish/French disclaimer retains the article language.
- Fixed English `Guides` branding and Kindle format lines are marked English. A missing article CTA label uses English language metadata for its existing English fallback.
- Topic-card action names now use `aria-labelledby` referencing the visible action text and the card heading. This preserves the meaningful action/count/title accessible name and exposes the separate language-bearing text nodes, while retaining exactly one link per card.
- Index counts select singular only for one topic; plural labels cover two or zero topics.

## Files changed

- `app.py`: language selection/annotations, language-aware accessible-name references, and singular/plural labels.
- `templates/guides_index.html`: English branding annotation.
- `templates/guide_topic.html`: UI-language annotations without changing content inheritance.
- `templates/hub_article.html`: UI/editorial/format annotations and content-language disclosure override.
- `tests/test_public_guides_accessibility.py`: 12 new regression cases plus an inherited-language assertion helper. Coverage includes French-only index fallback, mixed-language card accessible names, English/Spanish singular/plural/zero counts, French topic fallback, Spanish/French articles, and a non-search Spanish article with fallback English chrome.
- `tests/test_search_guides.py`: assert the same single-link, meaningful Spanish accessible name through its language-bearing `aria-labelledby` references.
- `.superpowers/sdd/final-fix-report.md`: this report.

## RED evidence

Tests were added before implementation. From the worktree:

```text
$ python3 -m pytest tests/test_public_guides_accessibility.py tests/test_search_guides.py -q
FF.F.FFFFFFF................................F......                      [100%]
11 failed, 40 passed in 1.10s
```

Expected failures:

- French index chrome/title inherited `fr` instead of `en`.
- French card lacked language-bearing accessible-name references.
- Plural counts rendered `2 tema`, `2 topic`, and `0 topic`.
- French topic skip link and chrome inherited `fr` instead of `en`.
- Spanish article editorial inherited `es` instead of `en`.
- French article fallback skip link inherited `fr` instead of `en`.
- Non-search Spanish article fallback skip link inherited `es` instead of `en`.
- Spanish index English branding inherited `es` instead of `en`.
- The updated single-link accessible-name contract lacked its required `aria-labelledby` references.

The initial card regression used Python membership against a BeautifulSoup tag when checking attribute absence. Self-review corrected it to `has_attr`; the missing accessible-name-reference failure remained valid.

## GREEN evidence

```text
$ python3 -m pytest tests/test_search_guides.py tests/test_public_guides_accessibility.py tests/test_audit_public_guides.py -q
....................................................................     [100%]
68 passed in 1.10s

$ python3 -m pytest tests/test_search_guides.py tests/test_public_guides_accessibility.py tests/test_audit_public_guides.py tests/test_growth_routes.py tests/test_content_hub.py -q
........................................................................ [ 42%]
........................................................................ [ 85%]
.........................                                                [100%]
169 passed in 2.35s

$ git diff --check
[no output; exit 0]
```

## Self-review and scope

- Reviewed every changed application/template line against the two assigned findings. Changes are restricted to rendering metadata, accessible-name structure, and topic count copy.
- All dynamic attributes, IDs, titles, descriptions, and language values continue through escaping; no raw content insertion was introduced.
- Routing, canonical links, JSON-LD, signed tracking tokens, search/direct attribution, qualified-view filtering, article eligibility, and the one tracked Amazon CTA are unchanged and covered by the 169-test run.
- No inventory, publication approval, KDP, Pinterest, dependency, service, environment, production data, shared control surface, or ownership-lock changes.
- No remaining concern identified within the assigned findings. This fix verifies rendered HTML inheritance and existing audit-runner contracts; it does not claim a fresh real-browser/assistive-technology audit. Parent retains responsibility for deployment, root memory, and project ownership lifecycle.
