# Competing on agentic search

**What the Exa 3.0 benchmark table actually measures, and where Jambubrowser can
realistically appear on it.**

Benchmark table source: Exa, *"Introducing Exa 3.0"*, 19 October 2026
(<https://exa.ai/blog/exa-3>). Reference harness: `github.com/exa-labs/benchmarks`
(MIT). Evaluated October 2026, GPT-6 Luna agent, 25 search rounds, 10 results
per search.

---

## 1. First: the table is about *search*, not scraping

Every row is a **search** benchmark, and the thing being compared is the
**search backend**, with the agent held fixed. That reframes the target: a
browser is not a competitor to Exa — a **retrieval system** is.

| Benchmark | Metric | What it rewards |
|---|---|---|
| BrowseComp | accuracy (LLM judge) | hard, verifiable factual questions |
| DSQA | F1 over answer items | deep-research comprehensiveness |
| WideSearch | row-level F1 over tables | broad "wide" information seeking |
| ATLAS | row F1 over entity tables | long-horizon entity discovery |
| WebCode | groundedness / accuracy | code retrieval for agents |
| FinSearchComp | accuracy (LLM judge) | expert-level financial lookup |
| Company / People FindAll | mean passed entities per task | **entity recall at scale** |

**A caveat about the source.** Exa's homepage carries a *different* table for
the same claim (e.g. Parallel's BrowseComp 72.7 there vs 63.1 in the blog
image), and labels DSQA and WideSearch as "accuracy %" while its own blog
footnote calls them F1. Neither table publishes confidence intervals or run
artifacts. There is no independent reproduction. If you cite this table, cite
the blog image and say a conflicting table exists on the homepage.

## 2. Second: Exa's lead is *index scale*, and you cannot match it

Exa's own stated reasons:

1. "Trained a larger embedding model with more compute and data."
2. "Made Dynamic Highlights generally available, using **95% fewer tokens** than
   full page text" — a model that *selects tokens across all results*, giving
   more to the best pages.
3. "**Added billions of documents to our index**" — 1.27B ecommerce listings,
   211M+ local businesses, real-time news/sports/markets. "Exa runs its own
   crawler and an independent index. It tracks over 2 trillion URLs, serves 100
   billion pages."

Point 3 is the moat. A crawler-based browser cannot out-scale an index that
serves 100 billion pages. **Trying to compete on index scale is a losing
strategy.** But point 2 is interesting, and it is a *token* result — which is
where Jambubrowser already has an advantage.

## 3. The lane that is actually open

**BrowseComp-Plus** (arXiv 2508.06600, ACL 2026) is BrowseComp rebuilt on a
fixed, human-verified 100K-document corpus, which removes live-web
nondeterminism and lets retrievers be swapped with the agent held fixed. It is
also fully local. Published results:

| System | Accuracy |
|---|---|
| **Search-R1 (open-source) + BM25** | **3.86%** |
| GPT-5 + BM25 | 55.9% |
| **GPT-5 + local Qwen3-Embedding-8B retriever** | **70.1%** |
| GPT-5 via Google Search API (live web) | 59.9% |

Two things follow, and they are the whole strategy:

- **A local embedding retriever beats live-web Google Search** (70.1% vs 59.9%)
  on a contamination-free benchmark. Local and private is not a consolation
  prize here — it can be *better*.
- **A naive lexical retriever scores 3.86%.** The gap between BM25 and an
  embedding retriever is the entire game. Exa and the others are shipping
  embedding retrieval; so should we.

No open-source or local system appears on the Artificial Analysis Search Index
either (Exa 80, Perplexity 80, Parallel 75, Brave 75, Firecrawl 73, model-only
baseline 33). **The local lane is empty.**

## 4. What we built, and what it measured

A protocol-faithful benchmark harness, so the claim is measured rather than
asserted:

- `backend/bench/runner.py` — the reference protocol: 25 rounds, 10 results per
  search, a terminal submit, `degraded` marking on budget exhaustion,
  oldest-evidence dropping on context overflow, and 95% bootstrap CIs over task
  resamples with seed 0.
- `backend/bench/grader.py` — **deterministic** graders: exact match, F1 over
  answer items, entity recall, and table F1. No judge model, so a run is free,
  reproducible, and offline.
- `backend/bench/corpus.py` — a local corpus with a BM25 index.
- `backend/modules/retrieval.py` — the retrieval layer: query expansion,
  cross-engine dedup, and blended ranking.
- `make bench` / `jambu bench --compare`.

### The measured result

```
suite: local          tasks: 16   mean: 0.7292   95% CI: [0.5000, 0.9375]
by metric: entities=1.5000, exact=0.8182, f1_items=0.5556

— comparison —
  naive                        0.6667  CI95 [0.4375, 0.8750]
  retrieval_no_expansion       0.6667  CI95 [0.4375, 0.8750]
```

**Retrieval beats naive by +0.0625, and the gain is attributable to query
expansion** (expansion-off scores identically to naive). Dedup and ranking are
neutral *on this corpus* — they matter when the engine chain returns the same
document several times, which this corpus only partly simulates.

**Be honest about the size of that number: 16 tasks, one task's worth of
difference, and the confidence intervals overlap heavily.** It is a direction,
not a result.

### Three things the benchmark taught us, which are worth more than the score

1. **Re-ranking can be a downgrade.** A first version replaced the backend's own
   ordering with term overlap and *lost* 6 points (0.5875 vs 0.6500). Overlap
   is cruder than BM25. The fix was to blend backend position with match
   strength rather than replace one with the other. **A retrieval change that
   isn't measured is a guess, and this one would have shipped as a regression.**

2. **The f1 grader was wrong before the data was.** It consumed one response
   item per gold item, which under-counts an answer that packs several facts
   into one line. The DSQA rubric judges each gold item *independently*; the
   grader now does too.

3. **Lexical retrieval cannot answer the hard questions.** The four tasks the
   local retriever still fails (`t01` "second generation gripper" → must
   distinguish G2 from the legacy G1; `t03` "CTO" → the document says "Chief
   Technology Officer") are precisely the cases a **semantic** retriever
   handles. We did not paper over them by adding "second generation → G2" to
   the thesaurus — that would be overfitting the eval and would not generalise.

### Dense and hybrid retrieval — measured

`backend/modules/semantic.py` adds a dense backend and a fusion layer, so the
claim can be tested rather than asserted:

- **`LsaEmbedder`** — TF-IDF + truncated SVD over the corpus. A real dense
  representation, computable with numpy alone, so the dense path is measurable
  offline.
- **`SentenceTransformerEmbedder`** — the neural encoder. The project already
  depends on `sentence-transformers` (the RAG store uses `all-MiniLM-L6-v2`), so
  this adds no new dependency — but it needs the model on disk, which this
  machine does not have.
- **`HybridIndex`** — reciprocal rank fusion, with a per-list weight.

`jambu bench --retriever-compare`, same policy and same grader:

| Retriever | Score | 95% CI |
|---|---|---|
| **lexical** (BM25 + expansion) | **0.7292** | [0.5000, 0.9375] |
| dense (LSA) | 0.6153 | [0.3875, 0.8375] |
| hybrid (RRF, dense weight 0.5) | 0.7292 | [0.5000, 0.9375] |

**Read this honestly: the dense backend contributed nothing, and at equal weight
it actively hurt** (hybrid at weight 1.0 scored **0.5417**, well below lexical
alone). Two reasons, and the second is the important one:

1. A 44-document corpus has no co-occurrence statistics worth learning. LSA
   needs a corpus; this is one.
2. **Fusing a strong ranker with a weak one at equal weight makes things
   worse**, because the weak side promotes documents the strong side had
   already rejected. Hence the damping weight (0.5), which restores parity
   rather than beating it.

So the local suite is good at **detecting regressions** — it caught the
re-ranking regression, the grader bug, and would catch a broken fusion — and
**cannot demonstrate a semantic win**. That claim needs the neural encoder on
a corpus large enough for it to mean something.

### The real benchmark, loaded and running

`backend/bench/suites.py` loads **BrowseComp-Plus** (`Tevatron/browsecomp-plus`),
the one public benchmark with a local system on it:

- The queries and answers are **canary-encrypted**. The loader decrypts them in
  memory using the key the benchmark authors publish, and **never writes
  decrypted content inside the repository** — `cache_dir()` refuses a
  `JAMBU_BENCH_CACHE` that points into the repo, and there is a test for it.
- The **published protocol** is honoured: *top-5 documents per search, 512
  tokens of context* ("across all methods for fair comparison").
- The **official metric is an LLM judge**, which is neither free nor
  reproducible. We do not reproduce it. Instead:
  - `answer_in_text` — the strict, deterministic question: *is the gold answer
    present in the context the agent was given?* An agent cannot answer from a
    context that lacks the answer, so it is a sound upper bound.
  - **judge-free retrieval metrics** — recall@5, gold-hit@5 and nDCG@5 against
    the benchmark's own relevance judgements (gold = 2, evidence = 1).

`jambu bench --suite browsecomp-plus --limit 10 --results-per-search 5`, run
against the live dataset:

| Retriever | answer-in-text | recall@5 | gold-hit@5 | nDCG@5 |
|---|---|---|---|---|
| **lexical** | **0.30** | **0.112** | 0.20 | **0.135** |
| dense (LSA) | 0.00 | 0.070 | 0.10 | 0.073 |
| hybrid (RRF) | 0.30 | 0.075 | 0.20 | 0.089 |

**These are not comparable to the published 70.1 / 59.9 / 3.86.** That
comparison uses the full 830-query, ~100K-document corpus, an LLM agent reading
the retrieved pages, and a judge. This run is 10 queries over a 226-document
corpus with no LLM in the loop. What it *is*: proof that the pipeline is real
and reproducible against the genuine dataset, and a regression gate for the
retrieval work.

The lexical-beats-dense pattern repeats here, for the same reason: a 226-document
corpus is still a corpus with nothing to learn co-occurrence statistics from.

## 5. The plan, in priority order

1. **Evaluate the neural encoder on BrowseComp-Plus.** The dataset is
   downloadable (`Tevatron/browsecomp-plus` on Hugging Face, a fixed
   human-verified ~100K-document corpus with mined hard negatives), it is
   contamination-free by construction, and it is the one public benchmark with
   a local system on it. Jambubrowser already ships the dependency and the
   model name. This is the measurement that would tell us whether the local
   lane really opens up — 70.1% with a local encoder vs 59.9% for live-web
   Google Search.
2. **Cross-round evidence accumulation.** The protocol allows 25 rounds; a
   FindAll-style task needs entities gathered across many pages, deduplicated.
   We have the receipts and the typed extraction to do that safely.
3. **Dynamic highlights.** Exa's 95%-token-reduction claim. Our compact and
   delta snapshots are the same idea, already built — but measured against a
   search workload, not a browser one.
4. **Code retrieval.** WebCode is a row and we have `search_code` (GitHub) and
   `search_academic` already. We are not competing on it yet.

## 6. Where this leaves the competitive claim

| | Firecrawl (the comparison we started from) | Exa / Perplexity / Brave | Jambu |
|---|---|---|---|
| Primary product | scraping API | search API | agent platform + local retrieval |
| Index | crawl-on-demand | owned, billions of docs | crawl-on-demand |
| Privacy | cloud | cloud | **local, no egress** |
| Licence | AGPL-3.0 | proprietary | MIT |
| Untrusted-content handling | opt-in, +4 credits, JSON-only | n/a | on by default, free |
| Fetch receipts | no | no | yes |
| Agentic-search benchmark | no | **yes** | starting (this work) |

**The honest claim we can make today:** Jambubrowser is a local, MIT-licensed
agent platform whose retrieval is measurably better than a naive baseline and
whose untrusted-content handling no competitor ships. **The honest claim we
cannot make yet:** that it would score in that table. It would not today —
we have no index, and the benchmarks reward recall over a corpus we do not
have. The table is a *destination*, and the work above is the road to the
first credible row in it.

---

## 7. Two evaluation systems, and why

The repository now contains two evaluation harnesses. They are not duplicates,
and the distinction is worth stating so nobody has to reverse-engineer it:

- **`backend/eval/`** — a general *agent task* harness. Tasks, providers,
  runners, metrics and reports; it already ships a `webarena-mini` suite that
  exercises `web_search` / `scrape_url`. Grading is provider-based.
- **`backend/bench/`** — a *retrieval* harness. Swap-able search backends,
  corpora, relevance judgements, deterministic graders and judge-free metrics
  (recall@k, gold-hit@k, nDCG@k). It exists because the published numbers are
  retrieval numbers: the benchmark swaps the search backend and holds the agent
  fixed.

If the two ever need to meet, the bridge is small: `backend/bench` produces
graded results, and `backend/eval` knows how to run and report agent tasks.

---

## 8. The agent in the loop

`backend/bench/agent.py` adds the piece the published scores actually measure:
a model driving the search, rather than a scripted policy.

- Two tools, matching the reference protocol: `search(query, top_k)` and
  `get_document(docid)`. The agent stops when it calls the final tool, when the
  round budget runs out, or when the model stops producing a usable action.
- **Untrusted content stays in a labelled channel.** Retrieved documents are
  attacker-influenced text; the system prompt states that document content is
  data and never instructions, and the content is embedded **JSON-encoded** so a
  document cannot close a quote and continue the instruction. That is the
  pattern the research recommends for indirect injection.
- The model comes from the engine's own provider registry, so the agent obeys
  `JAMBU_LLM_LOCAL_ONLY` like everything else — a benchmark that quietly called
  a cloud model would not be measuring the local system.
- A **model failure is reported as an error, not a zero score.** "The model was
  unavailable" and "the agent got every answer wrong" are different facts, and
  only one of them is a retriever problem.

**What is not verified:** the agent path has never run against a real model
here — the configured provider's key is exhausted in this environment, so a run
correctly reports 16 errors. The loop, the tool protocol, the untrusted-content
channel and the failure modes are covered by tests with a scripted model, but
the end-to-end agent number is pending a working provider — the same blocker as
the browser-rendering path.
