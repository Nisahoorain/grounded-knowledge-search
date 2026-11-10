# Decisions

## Retrieval default

Production retrieval is chunk-level RRF (`rrf_constant=60`, BM25 weight 1), then one chunk per page. `search` and `ask` use that path. Company and year from the question narrow the filings before either retriever runs.

## Reranker is opt-in

`search --rerank` and `ask --rerank` run page dedupe first, then blend the fusion rank with a cross-encoder rank (`cross-encoder/ms-marco-MiniLM-L-6-v2`, `max_length=256`, text cut at 1200 characters, pool of 20). The flag is off unless passed.

| set | mode | Hit | MRR | blend mean |
|---|---|---:|---:|---|
| dev n=20 | chunk+dedupe1 | 0.60 | 0.391 | |
| dev n=20 | dedupe+blend | 0.70 | 0.351 | 995 ms (max 1315) |
| holdout n=10 | chunk+dedupe1 | 1.00 | 0.507 | |
| holdout n=10 | dedupe+blend | 0.90 | 0.770 | 927 ms (max 1023) |

Model load from cache was 3.6 s. The earlier reranker, which let the cross-encoder order the list by itself, was 3.6 s per 30 pairs and dropped a rank-1 hit. The blend is about one second and kept `fb_03069` inside the top 10, but the dev mean did not land under 1 s and the slowest call was 1.3 s. On the ten questions that were not used for tuning, the blend raised MRR and lost a hit the default had found (`gks_007`). That is the accuracy side of the same tradeoff: better order on hits the blend already agrees with, and a miss where the default was right. It stays off the default path.
