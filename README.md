# dgsi-crawler

A crawler and extraction pipeline that turns Portugal's public court-rulings
database ([DGSI](https://www.dgsi.pt)) into a structured, auditable dataset.

I built it in 2026 as a freelance project for a documentary research team that
needed to answer one question across hundreds of appellate rulings on sexual
offences: **when a court convicts, how often is the prison sentence suspended?**
DGSI has the answer, but only as thousands of long, unstructured legal texts
behind a 1990s Lotus Domino search interface. This repo is the code that read
them. The dataset and findings belong to the client and are not included.

## What it does

```
dgsi/
  http.py      polite cached session: rate limiting, retry with backoff, on-disk cache
  search.py    descriptor search, paging past the server's silent 1000-row cap
  parse.py     ruling page -> structured fields (latin-1, <br>-split descriptors)
  classify.py  in-scope adults / minors / reject; filters the civil sense of "violação"
  extract.py   sentence + suspension: regex first pass, then a schema-bound LLM pass
build_pool.py       measure the full in-scope universe (one request per court x term)
draw_sample.py      seeded random draw from the pool (reproducible)
build_dataset.py    fetch, parse and classify the sample -> data/out/
dump_rulings.py     one full-text file per ruling, for the extraction pass
merge_extraction.py verify LLM output and merge it into the dataset
```

## What I learned building it

**The search engine lies, quietly.** Three traps, all found against the live server:
a field-scoped query also matches body text; accent-folding is not stemming, so pre-
and post-1990 spellings (*coação* / *coacção*) are different queries; and results cap
at 1,000 rows while *reporting* 1,000 as the total. Each one silently drops or adds
rulings without an error. `search.py` documents and handles all three.

**The obvious method was confidently wrong.** A regex for *"suspensão da execução da
pena"* looked like it worked and produced a plausible
headline rate. Checked by hand, it was under 25% precise: courts write that exact phrase
while explaining they *cannot* suspend a sentence over the five-year legal limit. The
error was systematic, not noise. The regex column is kept only so the disagreement
stays auditable.

**Verify what can be checked mechanically, and let it license the rest.** The LLM pass
must return a verbatim quote from the ruling for every decision. `merge_extraction.py`
refuses any row whose quote isn't found word-for-word in the source, or whose sentence
arithmetic contradicts itself. That one check caught an agent reading the wrong
ruling during the trial run.

**Never truncate the evidence.** The decision sits at the *end* of a ruling. An early
24k-character cap kept the preamble and dropped the verdict, which would have biased
the result while looking authoritative. Nothing in the pipeline truncates now.

**Run a trial slice first.** A 10-ruling trial cost about 5% of the full pass. It exposed
the regex's failure mode and two schema gaps that would otherwise have forced every
ruling to be re-read. Reading is the expensive step, so it should happen once.

## Run

```bash
pip install -r requirements.txt
python3 build_pool.py --out pool.csv          # ~66 searches, cached to data/cache/
python3 draw_sample.py --pool pool.csv        # seeded draw -> sample_100.csv
python3 build_dataset.py --sample sample_100.csv
python3 dump_rulings.py --sample sample_100.csv
# run the LLM extraction (build_dataset.py --llm with ANTHROPIC_API_KEY, or in-session
# agents reading data/rulings/), then:
python3 merge_extraction.py --journal path/to/journal.jsonl
```

The crawler waits 1.2s between live requests and caches every page, so re-runs never
hit the server again. Please keep it that way: DGSI is a public service.

## Caveats

- DGSI publishes only appellate and Supreme Court rulings, roughly 10% of cases and
  skewed toward the serious ones. Rates computed from it describe published appeals,
  not all Portuguese convictions.
- Extraction is verified by verbatim quote and legal arithmetic, but a small sample is
  still a small sample. Treat per-group rates as the shape, not a precise point.

Built with Python, requests, BeautifulSoup and Claude.
