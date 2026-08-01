# Web and news research

## Pipeline

plan → search (concurrent providers) → dedupe → filter → rank → extract → report

Providers: Google News RSS (en + ar), Bing News RSS, GDELT, DDGS. Each sits
behind one `SearchProvider` interface with its own circuit breaker and rate
limiter, so adding a paid adapter is one class.

A provider that fails contributes `ok=False` and the run continues. One failed
provider never invalidates the successful ones.

## Audited defects fixed

| Defect | Fix |
|---|---|
| "last 24 hours" enforced as **32 hours** (30h window + 2h future bound) | exact windows; boundary tests at 23.99h / 24.5h / 30h / 32h |
| Query split on `.`, so "U.S. tariffs" searched for **"U"** | topic extraction never splits; U.S., U.N., No. 10, 3.5%, Ph.D. covered by tests |
| "latest" silently meant 8 days | a default window marked `explicit=False` and labelled "last 7 days (default)" |
| Undated articles accepted under an explicit window | excluded, and the exclusion is reported |
| Only `pubDate` parsed | Atom `published`/`updated` and Dublin Core `dc:date` too |
| Naive timestamps assumed UTC, pushing UTC+3 publishers into the future | marked `date_verified=False` instead |
| First `<a href>` on the page used as the canonical | real canonical signals only |
| Headline matching with **no** score threshold | 0.6 Jaccard minimum |
| Homepages shown as articles | rejected |
| `lru_cache` with no TTL under a "current time" header | TTL cache |

## Source labelling

Every source carries: title, publisher, publication date (with "Date
unverified" when the timestamp had no offset), direct publisher URL, retrieval
provider, extraction method, extraction status — Full article read / Partial /
Headline-snippet only / Paywalled / Blocked / Could not be read — and an excerpt.

## Known limitation

Google News moved to opaque, server-resolved article ids (verified by decoding:
the `CBMi` payload is a token, not a URL). Aggregator links are resolved via
redirect params → canonical → fetch-redirect → exact-headline search; what
cannot be resolved is **labelled as still on the aggregator** rather than having
the aggregator pose as the publisher.
